"""
Entry point for running the application via `python -m fileuploader`.
"""

import os
from fileuploader.app import create_app


def main():
    """Run the Flask development server."""
    port = int(os.getenv("PORT", "3000"))
    app = create_app()
    app.run(host="0.0.0.0", port=port, debug=True)


if __name__ == "__main__":
    main()
