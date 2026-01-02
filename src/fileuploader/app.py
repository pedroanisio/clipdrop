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
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from werkzeug.utils import secure_filename

from fileuploader.crypto import encrypt_data, load_key_from_env, safe_decrypt

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

    # Apply any additional configuration
    if config:
        app.config.update(config)

    # Ensure upload and clipboard folders exist
    for folder in [app.config["UPLOAD_FOLDER"], app.config["CLIPBOARD_FOLDER"]]:
        os.makedirs(folder, exist_ok=True)

    # Register routes
    register_routes(app)

    # Setup scheduler for cleanup
    setup_scheduler(app)

    return app


def register_routes(app):
    """Register all application routes."""

    @app.route("/", methods=["GET", "POST"])
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
