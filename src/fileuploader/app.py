"""
Flask application factory and routes for the File Uploader & Clipboard Manager.
"""

import logging
import os
import uuid
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from flask import (
    Blueprint,
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_dance.consumer import oauth_authorized
from flask_dance.contrib.github import make_github_blueprint
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy.exc import IntegrityError
from werkzeug.utils import secure_filename

from fileuploader.crypto import encrypt_data, load_key_from_env, safe_decrypt
from fileuploader.extensions import db, login_manager
from fileuploader.models import OAuth, User

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
ALLOWED_EXTENSIONS = {
    "txt",
    "pdf",
    "png",
    "jpg",
    "jpeg",
    "gif",
    "pst",
    "md",
    "json",
    "zip",
    "tar",
    "py",
    "html",
    "css",
}

MIMETYPE_MAP = {
    "txt": "text/plain",
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "json": "application/json",
    "html": "text/html",
    "css": "text/css",
    "md": "text/markdown",
    "py": "text/x-python",
    "zip": "application/zip",
    "tar": "application/x-tar",
}

# Global encryption key (loaded once)
_encryption_key = None


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def get_encryption_key():
    """Get the encryption key, loading it if necessary."""
    global _encryption_key
    if _encryption_key is None:
        encryption_key_b64 = os.getenv("ENCRYPTION_KEY")
        if encryption_key_b64:
            try:
                _encryption_key = load_key_from_env(encryption_key_b64)
                logger.info("Encryption key loaded successfully")
            except Exception as e:
                logger.warning(f"Failed to load encryption key: {e}. Files will not be encrypted.")
        else:
            logger.warning("ENCRYPTION_KEY not set. Files will be stored unencrypted.")
    return _encryption_key


def create_app(config=None):
    """Application factory for creating Flask app instances."""
    # Determine the package directory for templates and static files
    package_dir = Path(__file__).parent

    app = Flask(
        __name__,
        template_folder=str(package_dir / "templates"),
        static_folder=str(package_dir / "static"),
    )

    # Load configuration
    secret_key = os.getenv("SECRET_KEY")
    if not secret_key:
        raise RuntimeError(
            "SECRET_KEY is not set. Run ./scripts/generate_secret.sh to create a .env file "
            "or export SECRET_KEY in the environment."
        )

    app.config["SECRET_KEY"] = secret_key
    app.config["UPLOAD_FOLDER"] = os.getenv("UPLOAD_FOLDER", "uploads")
    app.config["CLIPBOARD_FOLDER"] = os.getenv("CLIPBOARD_FOLDER", "clipboard")
    app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024 * 1024  # 10GB
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", "sqlite:///fileuploader.db")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Apply any additional configuration
    if config:
        app.config.update(config)

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message_category = "warning"

    with app.app_context():
        db.create_all()

    # Ensure upload and clipboard folders exist
    for folder in [app.config["UPLOAD_FOLDER"], app.config["CLIPBOARD_FOLDER"]]:
        os.makedirs(folder, exist_ok=True)

    register_auth(app)

    # Register routes
    register_routes(app)

    # Setup scheduler for cleanup
    setup_scheduler(app)

    return app


def register_auth(app):
    """Register authentication routes and OAuth configuration."""
    client_id = os.getenv("GITHUB_OAUTH_CLIENT_ID")
    client_secret = os.getenv("GITHUB_OAUTH_CLIENT_SECRET")
    oauth_enabled = bool(client_id and client_secret)
    app.config["OAUTH_ENABLED"] = oauth_enabled

    auth_bp = Blueprint("auth", __name__)

    @auth_bp.route("/login")
    def login():
        if current_user.is_authenticated:
            return redirect(url_for("upload_file"))
        return render_template("login.html", oauth_enabled=app.config["OAUTH_ENABLED"])

    @auth_bp.route("/logout")
    @login_required
    def logout():
        logout_user()
        flash("Signed out successfully.", "success")
        return redirect(url_for("auth.login"))

    app.register_blueprint(auth_bp)

    if not oauth_enabled:
        logger.warning("GitHub OAuth credentials are not configured.")
        return

    github_bp = make_github_blueprint(
        client_id=client_id,
        client_secret=client_secret,
        scope="read:user,user:email",
    )
    app.register_blueprint(github_bp, url_prefix="/login")

    @oauth_authorized.connect_via(github_bp)
    def github_logged_in(blueprint, token):
        if not token:
            flash("GitHub login failed. Please try again.", "danger")
            return False

        resp = blueprint.session.get("/user")
        if not resp.ok:
            flash("Could not fetch your GitHub profile.", "danger")
            return False

        github_info = resp.json()
        github_user_id = str(github_info.get("id", ""))
        if not github_user_id:
            flash("GitHub login did not return a user id.", "danger")
            return False

        username = github_info.get("login", "github-user")
        avatar_url = github_info.get("avatar_url")

        oauth = OAuth.query.filter_by(
            provider=blueprint.name,
            provider_user_id=github_user_id,
        ).first()
        user = User.query.filter_by(github_id=github_user_id).first()

        try:
            if user is None:
                user = User(
                    github_id=github_user_id,
                    username=username,
                    avatar_url=avatar_url,
                )
                db.session.add(user)
            else:
                user.username = username
                user.avatar_url = avatar_url

            if oauth is None:
                oauth = OAuth(
                    provider=blueprint.name,
                    provider_user_id=github_user_id,
                    token=token,
                    user=user,
                )
                db.session.add(oauth)
            else:
                oauth.token = token
                oauth.user = user

            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            user = User.query.filter_by(github_id=github_user_id).first()
            oauth = OAuth.query.filter_by(
                provider=blueprint.name,
                provider_user_id=github_user_id,
            ).first()
            if user and oauth:
                oauth.user = user
                oauth.token = token
                user.username = username
                user.avatar_url = avatar_url
                db.session.commit()
            else:
                flash("Sign in failed. Please try again.", "danger")
                return False

        login_user(user)
        flash("Signed in with GitHub.", "success")
        return False


def register_routes(app):
    """Register all application routes."""

    @app.route("/", methods=["GET", "POST"])
    @login_required
    def upload_file():
        if request.method == "POST":
            if "file" not in request.files:
                logger.error("No file part")
                return respond_upload(
                    "No file selected. Please choose a file to upload.",
                    "warning",
                    400,
                    request.url,
                )
            file = request.files["file"]
            if file.filename == "":
                logger.error("No selected file")
                return respond_upload(
                    "No file selected. Please choose a file to upload.",
                    "warning",
                    400,
                    request.url,
                )
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                filename = f"{uuid.uuid4()}_{filename}"
                try:
                    file_data = file.read()
                    file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
                    save_file_encrypted(file_data, file_path)
                    logger.info(
                        f"File {filename} successfully uploaded "
                        f"(encrypted: {get_encryption_key() is not None})"
                    )
                    return respond_upload(
                        "File successfully uploaded.",
                        "success",
                        200,
                        url_for("upload_file"),
                    )
                except Exception as e:
                    logger.error(f"File upload failed: {e}")
                    return respond_upload(
                        "Upload failed. Please try again.",
                        "danger",
                        500,
                        request.url,
                    )
            else:
                logger.warning("File type not allowed")
                return respond_upload(
                    "File type not allowed. Please choose a supported file.",
                    "warning",
                    400,
                    request.url,
                )

        files = [
            get_file_properties(f, app.config["UPLOAD_FOLDER"])
            for f in os.listdir(app.config["UPLOAD_FOLDER"])
        ]
        clipboard_files = [
            get_file_properties(f, app.config["CLIPBOARD_FOLDER"])
            for f in os.listdir(app.config["CLIPBOARD_FOLDER"])
        ]
        return render_template(
            "index.html",
            files=files,
            clipboard_files=clipboard_files,
            allowed_extensions=ALLOWED_EXTENSIONS,
            active_page="files",
        )

    @app.route("/uploads/<filename>")
    @login_required
    def uploaded_file(filename):
        """Serve uploaded file, decrypting if necessary."""
        filename = secure_filename(filename)
        file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        if not os.path.exists(file_path):
            return jsonify({"status": "error", "message": "File not found"}), 404

        try:
            data = read_file_decrypted(file_path)
            ext = get_file_extension(filename)
            mimetype = MIMETYPE_MAP.get(ext, "application/octet-stream")
            return send_file(BytesIO(data), mimetype=mimetype, download_name=filename)
        except Exception as e:
            logger.error(f"Failed to serve file {filename}: {e}")
            return jsonify({"status": "error", "message": "Failed to read file"}), 500

    @app.route("/clipboard/<filename>")
    @login_required
    def clipboard_file(filename):
        """View clipboard content in a nice template."""
        file_path = os.path.join(app.config["CLIPBOARD_FOLDER"], filename)
        if not os.path.exists(file_path):
            return render_template(
            "clipboard_view.html", file=None, is_text=False, active_page="clipboard"
        )

        file_props = get_file_properties(filename, app.config["CLIPBOARD_FOLDER"])
        is_text = filename.endswith((".txt", ".md", ".json", ".py", ".html", ".css"))

        return render_template(
            "clipboard_view.html",
            file=file_props,
            is_text=is_text,
            active_page="clipboard",
        )

    @app.route("/clipboard/raw/<filename>")
    @login_required
    def clipboard_file_raw(filename):
        """Serve raw clipboard file for download, decrypting if necessary."""
        filename = secure_filename(filename)
        file_path = os.path.join(app.config["CLIPBOARD_FOLDER"], filename)
        if not os.path.exists(file_path):
            return jsonify({"status": "error", "message": "File not found"}), 404

        try:
            data = read_file_decrypted(file_path)
            ext = get_file_extension(filename)
            mimetype_map = {
                "txt": "text/plain",
                "png": "image/png",
                "jpg": "image/jpeg",
                "jpeg": "image/jpeg",
                "gif": "image/gif",
                "md": "text/markdown",
                "json": "application/json",
            }
            mimetype = mimetype_map.get(ext, "application/octet-stream")
            return send_file(BytesIO(data), mimetype=mimetype, download_name=filename)
        except Exception as e:
            logger.error(f"Failed to serve clipboard file {filename}: {e}")
            return jsonify({"status": "error", "message": "Failed to read file"}), 500

    @app.route("/shared-clipboard")
    @login_required
    def shared_clipboard():
        clipboard_files = [
            get_file_properties(f, app.config["CLIPBOARD_FOLDER"])
            for f in os.listdir(app.config["CLIPBOARD_FOLDER"])
        ]
        clipboard_files.sort(key=lambda x: x["creation_time"], reverse=True)
        return render_template(
            "shared_clipboard.html",
            clipboard_files=clipboard_files,
            active_page="clipboard",
        )

    @app.route("/clipboard", methods=["POST"])
    @login_required
    def clipboard():
        data = request.form["clipboard_data"]
        if "image" in request.files:
            file = request.files["image"]
            if file and allowed_file(file.filename):
                filename = f"clipboard_{uuid.uuid4()}_{secure_filename(file.filename)}"
                file_data = file.read()
                file_path = os.path.join(app.config["CLIPBOARD_FOLDER"], filename)
                save_file_encrypted(file_data, file_path)
                return jsonify(
                    {"status": "success", "message": "Image saved", "filename": filename}
                )
        else:
            filename = f"clipboard_{uuid.uuid4()}.txt"
            file_path = os.path.join(app.config["CLIPBOARD_FOLDER"], filename)
            save_file_encrypted(data.encode("utf-8"), file_path)
            return jsonify(
                {"status": "success", "message": "Text saved", "filename": filename}
            )

    @app.route("/delete/<path:filename>", methods=["DELETE"])
    @login_required
    def delete_file(filename):
        filename = secure_filename(filename)
        file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                logger.info(f"File {filename} deleted by user")
                return jsonify({"status": "success", "message": "File deleted"})
            except Exception as e:
                logger.error(f"Failed to delete file {filename}: {e}")
                return jsonify({"status": "error", "message": "Failed to delete file"}), 500
        return jsonify({"status": "error", "message": "File not found"}), 404

    @app.route("/delete/clipboard/<path:filename>", methods=["DELETE"])
    @login_required
    def delete_clipboard_file(filename):
        filename = secure_filename(filename)
        file_path = os.path.join(app.config["CLIPBOARD_FOLDER"], filename)
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                logger.info(f"Clipboard file {filename} deleted by user")
                return jsonify({"status": "success", "message": "Clipboard item deleted"})
            except Exception as e:
                logger.error(f"Failed to delete clipboard file {filename}: {e}")
                return jsonify(
                    {"status": "error", "message": "Failed to delete clipboard item"}
                ), 500
        return jsonify({"status": "error", "message": "Clipboard item not found"}), 404


def setup_scheduler(app):
    """Setup background scheduler for file cleanup."""

    def cleanup_files():
        now = datetime.now()
        for folder in [app.config["UPLOAD_FOLDER"], app.config["CLIPBOARD_FOLDER"]]:
            if not os.path.exists(folder):
                continue
            for filename in os.listdir(folder):
                file_path = os.path.join(folder, filename)
                file_creation_time = datetime.fromtimestamp(os.path.getctime(file_path))
                if now - file_creation_time > timedelta(days=1):
                    os.remove(file_path)
                    logger.info(f"Removed file {filename}")

    scheduler = BackgroundScheduler()
    scheduler.add_job(func=cleanup_files, trigger="interval", hours=24)
    scheduler.start()


# Helper functions


def wants_json_response():
    """Check if the request wants a JSON response."""
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def respond_upload(message, category, status, redirect_url):
    """Respond to upload requests with either JSON or redirect."""
    if wants_json_response():
        payload = {
            "status": "success" if status < 400 else "error",
            "message": message,
        }
        return jsonify(payload), status
    flash(message, category)
    return redirect(redirect_url)


def allowed_file(filename):
    """Check if a file extension is allowed."""
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def human_readable_size(size_bytes):
    """Convert bytes to human readable format."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} PB"


def get_file_extension(filename):
    """Get file extension from filename."""
    if "." in filename:
        return filename.rsplit(".", 1)[1].lower()
    return ""


def save_file_encrypted(data: bytes, file_path: str) -> None:
    """Save file with encryption if key is available."""
    key = get_encryption_key()
    if key:
        data = encrypt_data(data, key)
    with open(file_path, "wb") as f:
        f.write(data)


def read_file_decrypted(file_path: str) -> bytes:
    """Read file and decrypt if encrypted."""
    with open(file_path, "rb") as f:
        data = f.read()
    key = get_encryption_key()
    if key:
        data, _ = safe_decrypt(data, key)
    return data


def get_file_properties(filename, folder):
    """Get properties of a file."""
    file_path = os.path.join(folder, filename)
    file_size = os.path.getsize(file_path)
    file_creation_time = datetime.fromtimestamp(os.path.getctime(file_path))
    content = ""
    if filename.endswith(".txt"):
        try:
            data = read_file_decrypted(file_path)
            content = data.decode("utf-8")
        except (IOError, UnicodeDecodeError):
            content = "[Unable to read file content]"
    return {
        "name": filename,
        "size": file_size,
        "size_human": human_readable_size(file_size),
        "creation_time": file_creation_time,
        "content": content,
        "extension": get_file_extension(filename),
    }
