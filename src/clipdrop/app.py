"""
Flask application factory and routes for ClipDrop - File Uploader & Clipboard Manager.
"""

import logging
import os
import uuid
from collections import defaultdict
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
from flask_dance.consumer import oauth_authorized, oauth_error
from flask_dance.contrib.github import make_github_blueprint
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy.exc import IntegrityError
from werkzeug.utils import secure_filename

from clipdrop.crypto import encrypt_data, load_key_from_env, safe_decrypt
from clipdrop.extensions import db, login_manager
from clipdrop.models import ClipboardFolder, ClipboardItem, ClipboardTag, OAuth, User
from clipdrop.storage import get_storage, init_storage

# Load environment variables
load_dotenv()

# Allow OAuth over HTTP for local development (disable in production)
if os.getenv("FLASK_ENV") == "development" or os.getenv("OAUTHLIB_INSECURE_TRANSPORT"):
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

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
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", "sqlite:///clipdrop.db")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Apply any additional configuration
    if config:
        app.config.update(config)

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = None  # Disable default "Please log in" message
    login_manager.login_message_category = "warning"

    with app.app_context():
        db.create_all()

    # Initialize storage backend
    init_storage()

    register_auth(app)

    # Register routes
    register_routes(app)

    # Register error handlers
    register_error_handlers(app)

    # Setup scheduler for cleanup
    setup_scheduler(app)

    return app


def register_error_handlers(app):
    """Register error handlers for the application."""
    from oauthlib.oauth2.rfc6749.errors import OAuth2Error

    @app.errorhandler(OAuth2Error)
    def handle_oauth_error(error):
        """Handle OAuth2 errors gracefully."""
        error_messages = {
            "bad_verification_code": "The login code has expired. Please try signing in again.",
            "access_denied": "GitHub access was denied. Please try again.",
            "invalid_grant": "The authorization code has expired. Please try signing in again.",
        }
        error_type = getattr(error, "error", str(error))
        user_message = error_messages.get(error_type, f"Authentication failed. Please try again.")
        logger.warning(f"OAuth2 error caught: {error}")
        flash(user_message, "danger")
        return redirect(url_for("auth.login"))


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

    @oauth_error.connect_via(github_bp)
    def github_error(blueprint, message, response):
        """Handle OAuth errors gracefully."""
        error_messages = {
            "bad_verification_code": "The login code has expired. Please try signing in again.",
            "access_denied": "GitHub access was denied. Please try again.",
            "incorrect_client_credentials": "OAuth configuration error. Please contact the administrator.",
        }
        # Extract error type from message or response
        error_type = message if isinstance(message, str) else "unknown"
        user_message = error_messages.get(error_type, f"GitHub authentication failed: {message}")
        logger.warning(f"OAuth error: {message}")
        flash(user_message, "danger")
        return redirect(url_for("auth.login"))

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
                    encrypted_data = encrypt_file_data(file_data)
                    storage = get_storage()
                    storage.save(filename, encrypted_data, content_type=file.mimetype)
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

        storage = get_storage()
        files = [get_file_properties(f) for f in storage.list_files()]
        return render_template(
            "index.html",
            files=files,
            allowed_extensions=ALLOWED_EXTENSIONS,
            active_page="files",
        )

    @app.route("/uploads/<filename>")
    @login_required
    def uploaded_file(filename):
        """Serve uploaded file, decrypting if necessary."""
        filename = secure_filename(filename)
        storage = get_storage()
        if not storage.exists(filename):
            return jsonify({"status": "error", "message": "File not found"}), 404

        try:
            encrypted_data = storage.read(filename)
            data = decrypt_file_data(encrypted_data)
            ext = get_file_extension(filename)
            mimetype = MIMETYPE_MAP.get(ext, "application/octet-stream")
            return send_file(BytesIO(data), mimetype=mimetype, download_name=filename)
        except Exception as e:
            logger.error(f"Failed to serve file {filename}: {e}")
            return jsonify({"status": "error", "message": "Failed to read file"}), 500

    @app.route("/clipboard/<int:item_id>")
    @login_required
    def clipboard_file(item_id):
        """View clipboard content."""
        item = ClipboardItem.query.filter_by(id=item_id, user_id=current_user.id).first()
        if not item:
            return render_template(
                "clipboard_view.html", item=None, is_text=False, active_page="clipboard"
            )

        item_data = serialize_clipboard_item(item)
        return render_template(
            "clipboard_view.html",
            item=item_data,
            is_text=item_data["is_text"],
            active_page="clipboard",
        )

    @app.route("/clipboard/<int:item_id>/raw")
    @login_required
    def clipboard_file_raw(item_id):
        """Serve raw clipboard content for download."""
        item = ClipboardItem.query.filter_by(id=item_id, user_id=current_user.id).first()
        if not item:
            return jsonify({"status": "error", "message": "Item not found"}), 404

        try:
            data = decrypt_clipboard_data(item.content)
            return send_file(
                BytesIO(data),
                mimetype=item.content_type or "application/octet-stream",
                download_name=item.name,
            )
        except Exception as e:
            logger.error(f"Failed to serve clipboard item {item_id}: {e}")
            return jsonify({"status": "error", "message": "Failed to read item"}), 500

    @app.route("/shared-clipboard")
    @login_required
    def shared_clipboard():
        folder_id = request.args.get("folder_id", type=int)
        current_folder = None
        breadcrumbs = []
        if folder_id:
            current_folder = ClipboardFolder.query.filter_by(
                id=folder_id, user_id=current_user.id
            ).first()
            if not current_folder:
                flash("Folder not found.", "warning")
                return redirect(url_for("shared_clipboard"))
            breadcrumbs = build_folder_breadcrumbs(current_folder)

        subfolders = (
            ClipboardFolder.query.filter_by(user_id=current_user.id, parent_id=folder_id)
            .order_by(ClipboardFolder.name.asc())
            .all()
        )
        items = (
            ClipboardItem.query.filter_by(user_id=current_user.id, folder_id=folder_id)
            .order_by(ClipboardItem.created_at.desc())
            .all()
        )
        clipboard_items = [serialize_clipboard_item(item) for item in items]
        return render_template(
            "shared_clipboard.html",
            clipboard_items=clipboard_items,
            folders=subfolders,
            current_folder=current_folder,
            breadcrumbs=breadcrumbs,
            active_page="clipboard",
        )

    @app.route("/clipboard", methods=["POST"])
    @login_required
    def clipboard():
        folder_id = request.form.get("folder_id", type=int)
        if folder_id:
            folder = ClipboardFolder.query.filter_by(
                id=folder_id, user_id=current_user.id
            ).first()
            if not folder:
                return jsonify({"status": "error", "message": "Folder not found"}), 404

        tags = parse_tag_names(request.form.get("tags", ""))
        favorite = request.form.get("favorite") == "on"
        keep = request.form.get("keep") == "on"
        expires_at = None if keep else datetime.utcnow() + timedelta(days=1)

        if "image" in request.files and request.files["image"].filename:
            file = request.files["image"]
            if not allowed_file(file.filename):
                return jsonify({"status": "error", "message": "File type not allowed"}), 400
            filename = secure_filename(file.filename)
            name = f"clipboard_{uuid.uuid4()}_{filename}" if filename else f"clipboard_{uuid.uuid4()}"
            raw_data = file.read()
            content_type = file.mimetype or "application/octet-stream"
            is_text = False
        else:
            data = request.form.get("clipboard_data", "").strip()
            if not data:
                return jsonify({"status": "error", "message": "Clipboard text is required"}), 400
            raw_data = data.encode("utf-8")
            name = f"clipboard_{uuid.uuid4()}.txt"
            content_type = "text/plain"
            is_text = True

        item = ClipboardItem(
            user_id=current_user.id,
            folder_id=folder_id,
            name=name,
            content=encrypt_clipboard_data(raw_data),
            content_type=content_type,
            is_text=is_text,
            size=len(raw_data),
            favorite=favorite,
            expires_at=expires_at,
        )
        item.tags = get_or_create_tags(tags, current_user.id)
        db.session.add(item)
        db.session.commit()
        return jsonify({"status": "success", "message": "Saved", "id": item.id})

    @app.route("/clipboard/<int:item_id>/edit", methods=["GET", "POST"])
    @login_required
    def edit_clipboard_item(item_id):
        item = ClipboardItem.query.filter_by(id=item_id, user_id=current_user.id).first()
        if not item:
            flash("Clipboard item not found.", "warning")
            return redirect(url_for("shared_clipboard"))

        if request.method == "POST":
            name = request.form.get("name", "").strip()
            name = normalize_clipboard_name(name, item.name, item.is_text)
            folder_id = request.form.get("folder_id", type=int)
            if folder_id:
                folder = ClipboardFolder.query.filter_by(
                    id=folder_id, user_id=current_user.id
                ).first()
                if not folder:
                    flash("Folder not found.", "warning")
                    return redirect(url_for("edit_clipboard_item", item_id=item_id))

            tags = parse_tag_names(request.form.get("tags", ""))
            favorite = request.form.get("favorite") == "on"
            keep = request.form.get("keep") == "on"

            if item.is_text:
                content_text = request.form.get("content", "")
                raw_data = content_text.encode("utf-8")
                item.content = encrypt_clipboard_data(raw_data)
                item.size = len(raw_data)

            item.name = name
            item.folder_id = folder_id
            item.favorite = favorite
            item.expires_at = None if keep else datetime.utcnow() + timedelta(days=1)
            item.tags = get_or_create_tags(tags, current_user.id)

            db.session.commit()
            flash("Clipboard item updated.", "success")
            return redirect(url_for("shared_clipboard", folder_id=item.folder_id))

        content_text = ""
        if item.is_text:
            content_text = decrypt_clipboard_data(item.content).decode("utf-8", errors="replace")

        return render_template(
            "clipboard_edit.html",
            item=item,
            content_text=content_text,
            folder_options=build_folder_options(current_user.id),
            tags_value=", ".join(tag.name for tag in item.tags),
            active_page="clipboard",
        )

    @app.route("/delete/<path:filename>", methods=["DELETE", "POST"])
    @login_required
    def delete_file(filename):
        filename = secure_filename(filename)
        storage = get_storage()
        if storage.exists(filename):
            try:
                storage.delete(filename)
                logger.info(f"File {filename} deleted by user")
                return jsonify({"status": "success", "message": "File deleted"})
            except Exception as e:
                logger.error(f"Failed to delete file {filename}: {e}")
                return jsonify({"status": "error", "message": "Failed to delete file"}), 500
        return jsonify({"status": "error", "message": "File not found"}), 404

    @app.route("/clipboard/<int:item_id>/delete", methods=["POST"])
    @login_required
    def delete_clipboard_item(item_id):
        item = ClipboardItem.query.filter_by(id=item_id, user_id=current_user.id).first()
        if not item:
            return jsonify({"status": "error", "message": "Clipboard item not found"}), 404
        db.session.delete(item)
        db.session.commit()
        return jsonify({"status": "success", "message": "Clipboard item deleted"})

    @app.route("/clipboard/<int:item_id>/favorite", methods=["POST"])
    @login_required
    def toggle_clipboard_favorite(item_id):
        item = ClipboardItem.query.filter_by(id=item_id, user_id=current_user.id).first()
        if not item:
            return jsonify({"status": "error", "message": "Clipboard item not found"}), 404
        payload = request.get_json(silent=True) or {}
        favorite = payload.get("favorite")
        if favorite is None:
            favorite = not item.favorite
        item.favorite = bool(favorite)
        db.session.commit()
        return jsonify({"status": "success", "favorite": item.favorite})

    @app.route("/clipboard/<int:item_id>/retention", methods=["POST"])
    @login_required
    def toggle_clipboard_retention(item_id):
        item = ClipboardItem.query.filter_by(id=item_id, user_id=current_user.id).first()
        if not item:
            return jsonify({"status": "error", "message": "Clipboard item not found"}), 404
        payload = request.get_json(silent=True) or {}
        keep = payload.get("keep")
        if keep is None:
            keep = item.expires_at is not None
        item.expires_at = None if keep else datetime.utcnow() + timedelta(days=1)
        db.session.commit()
        return jsonify({"status": "success", "kept": item.expires_at is None})

    @app.route("/clipboard/folders", methods=["POST"])
    @login_required
    def create_clipboard_folder():
        name = request.form.get("name", "").strip()
        if not name:
            return jsonify({"status": "error", "message": "Folder name is required"}), 400
        parent_id = request.form.get("parent_id", type=int)
        if parent_id:
            parent = ClipboardFolder.query.filter_by(
                id=parent_id, user_id=current_user.id
            ).first()
            if not parent:
                return jsonify({"status": "error", "message": "Parent folder not found"}), 404

        folder = ClipboardFolder(user_id=current_user.id, name=name, parent_id=parent_id)
        db.session.add(folder)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return jsonify(
                {"status": "error", "message": "Folder name already exists"}
            ), 400
        return jsonify({"status": "success", "id": folder.id})

    @app.route("/clipboard/folders/<int:folder_id>/rename", methods=["POST"])
    @login_required
    def rename_clipboard_folder(folder_id):
        folder = ClipboardFolder.query.filter_by(id=folder_id, user_id=current_user.id).first()
        if not folder:
            return jsonify({"status": "error", "message": "Folder not found"}), 404
        payload = request.get_json(silent=True) or {}
        name = (payload.get("name") or "").strip()
        if not name:
            return jsonify({"status": "error", "message": "Folder name is required"}), 400
        folder.name = name
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return jsonify(
                {"status": "error", "message": "Folder name already exists"}
            ), 400
        return jsonify({"status": "success", "name": folder.name})

    @app.route("/clipboard/folders/<int:folder_id>/delete", methods=["POST"])
    @login_required
    def delete_clipboard_folder(folder_id):
        folder = ClipboardFolder.query.filter_by(id=folder_id, user_id=current_user.id).first()
        if not folder:
            return jsonify({"status": "error", "message": "Folder not found"}), 404
        if folder.children or folder.items:
            return jsonify(
                {"status": "error", "message": "Folder is not empty"}
            ), 400
        db.session.delete(folder)
        db.session.commit()
        return jsonify({"status": "success"})


def setup_scheduler(app):
    """Setup background scheduler for file cleanup."""

    def cleanup_files():
        now = datetime.utcnow()

        # Clean up uploaded files older than 24 hours using storage backend
        try:
            storage = get_storage()
            deleted = storage.delete_older_than("", timedelta(days=1))
            if deleted:
                logger.info(f"Cleaned up {len(deleted)} expired uploaded files")
        except Exception as e:
            logger.error(f"Failed to clean up uploaded files: {e}")

        # Clean up expired clipboard items from database
        with app.app_context():
            expired_items = (
                ClipboardItem.query.filter(ClipboardItem.expires_at.isnot(None))
                .filter(ClipboardItem.expires_at <= now)
                .all()
            )
            if expired_items:
                for item in expired_items:
                    db.session.delete(item)
                db.session.commit()
                logger.info(f"Cleaned up {len(expired_items)} expired clipboard items")

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


def encrypt_file_data(data: bytes) -> bytes:
    """Encrypt file data if encryption key is available."""
    key = get_encryption_key()
    if key:
        return encrypt_data(data, key)
    return data


def decrypt_file_data(data: bytes) -> bytes:
    """Decrypt file data if encrypted."""
    key = get_encryption_key()
    if key:
        data, _ = safe_decrypt(data, key)
    return data


def encrypt_clipboard_data(data: bytes) -> bytes:
    """Encrypt clipboard content if an encryption key is available."""
    key = get_encryption_key()
    if key:
        return encrypt_data(data, key)
    return data


def decrypt_clipboard_data(data: bytes) -> bytes:
    """Decrypt clipboard content if encrypted; return raw data otherwise."""
    key = get_encryption_key()
    if key:
        data, _ = safe_decrypt(data, key)
    return data


def parse_tag_names(raw_tags: str) -> list[str]:
    """Parse a comma-separated tag string into normalized tag names."""
    if not raw_tags:
        return []
    tags = []
    for raw in raw_tags.split(","):
        name = raw.strip()
        if name:
            tags.append(name.lower())
    return list(dict.fromkeys(tags))


def normalize_clipboard_name(name: str, fallback: str, is_text: bool) -> str:
    """Normalize clipboard item names and preserve extensions."""
    safe_name = secure_filename(name) if name else ""
    if not safe_name:
        safe_name = fallback
    if "." not in safe_name:
        ext = get_file_extension(fallback)
        if not ext and is_text:
            ext = "txt"
        if ext:
            safe_name = f"{safe_name}.{ext}"
    return safe_name


def get_or_create_tags(tag_names: list[str], user_id: int) -> list[ClipboardTag]:
    """Fetch existing tags and create missing ones for a user."""
    if not tag_names:
        return []
    existing = (
        ClipboardTag.query.filter(
            ClipboardTag.user_id == user_id,
            ClipboardTag.name.in_(tag_names),
        )
        .all()
    )
    existing_map = {tag.name: tag for tag in existing}
    new_tags = []
    for name in tag_names:
        if name in existing_map:
            continue
        tag = ClipboardTag(user_id=user_id, name=name)
        db.session.add(tag)
        new_tags.append(tag)
    if new_tags:
        db.session.flush()
        existing.extend(new_tags)
    return existing


def build_folder_options(user_id: int) -> list[dict]:
    """Build a list of folder options with indentation for select inputs."""
    folders = ClipboardFolder.query.filter_by(user_id=user_id).all()
    children_map: dict[int | None, list[ClipboardFolder]] = defaultdict(list)
    for folder in folders:
        children_map[folder.parent_id].append(folder)
    for child_list in children_map.values():
        child_list.sort(key=lambda f: f.name.lower())

    options = []

    def walk(parent_id: int | None, depth: int) -> None:
        for folder in children_map.get(parent_id, []):
            label = f"{'-' * depth} {folder.name}" if depth else folder.name
            options.append({"id": folder.id, "label": label})
            walk(folder.id, depth + 1)

    walk(None, 0)
    return options


def build_folder_breadcrumbs(folder: ClipboardFolder) -> list[ClipboardFolder]:
    """Return breadcrumb list from root to the current folder."""
    crumbs = []
    current = folder
    while current:
        crumbs.append(current)
        current = current.parent
    return list(reversed(crumbs))


def serialize_clipboard_item(item: ClipboardItem) -> dict:
    """Serialize clipboard item for template rendering."""
    content_text = ""
    if item.is_text:
        raw = decrypt_clipboard_data(item.content)
        content_text = raw.decode("utf-8", errors="replace")
    return {
        "id": item.id,
        "name": item.name,
        "extension": get_file_extension(item.name),
        "size": item.size,
        "size_human": human_readable_size(item.size),
        "creation_time": item.created_at,
        "favorite": item.favorite,
        "tags": [tag.name for tag in item.tags],
        "folder_id": item.folder_id,
        "is_text": item.is_text,
        "content": content_text,
        "expires_at": item.expires_at,
        "kept": item.expires_at is None,
    }


def get_file_properties(storage_file):
    """Get properties of a file from StorageFile object."""
    from clipdrop.storage import StorageFile

    filename = storage_file.key
    file_size = storage_file.size
    file_creation_time = storage_file.last_modified
    content = ""
    if filename.endswith(".txt"):
        try:
            storage = get_storage()
            encrypted_data = storage.read(filename)
            data = decrypt_file_data(encrypted_data)
            content = data.decode("utf-8")
        except (IOError, UnicodeDecodeError, Exception):
            content = "[Unable to read file content]"
    return {
        "name": filename,
        "size": file_size,
        "size_human": human_readable_size(file_size),
        "creation_time": file_creation_time,
        "content": content,
        "extension": get_file_extension(filename),
    }
