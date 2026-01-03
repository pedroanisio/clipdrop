# ClipDrop

ClipDrop is a Flask web app for authenticated file uploads and shared clipboard entries.
Upload files, save clipboard text or images, and share/download them from a single UI. Files can be
encrypted at rest and are cleaned up automatically every 24 hours.

## Features

- Drag-and-drop file uploads with download/delete actions
- Clipboard text and image capture with a dedicated viewer
- Favorites, tags, and folder organization for clipboard items
- Per-item retention control (keep beyond 24 hours)
- GitHub OAuth sign-in (required to access the app)
- AES-256-GCM encryption for files at rest (optional)
- Automatic cleanup of files after 24 hours
- Pluggable storage backends (local filesystem or S3-compatible)
- Production-ready for Digital Ocean App Platform

## Supported File Types

`txt`, `pdf`, `png`, `jpg`, `jpeg`, `gif`, `pst`, `md`, `json`, `zip`, `tar`, `py`, `html`, `css`

## Technology Stack

- Flask, Flask-Login, Flask-Dance
- Flask-SQLAlchemy + SQLite/PostgreSQL
- APScheduler
- boto3 (S3/Spaces storage)
- Bootstrap + JavaScript

## Setup and Installation

### Prerequisites

- Python 3.12+
- uv or pip

### Environment Variables

Copy `.env.example` to `.env` and configure:

```sh
cp .env.example .env
```

**Required variables:**
```
SECRET_KEY=your-unique-secret
GITHUB_OAUTH_CLIENT_ID=your-client-id
GITHUB_OAUTH_CLIENT_SECRET=your-secret
```

**Optional variables:**
```
ENCRYPTION_KEY=your-base64-encoded-key   # AES-256 encryption (recommended)
DATABASE_URL=sqlite:///clipdrop.db       # or postgresql://...
PORT=3000
```

**Storage configuration:**
```
STORAGE_TYPE=local                        # "local" or "s3"
UPLOAD_FOLDER=uploads                     # for local storage

# For S3/Spaces storage:
SPACES_BUCKET=your-bucket-name
SPACES_ENDPOINT=https://nyc3.digitaloceanspaces.com
SPACES_REGION=nyc3
SPACES_ACCESS_KEY=your-access-key
SPACES_SECRET_KEY=your-secret-key
SPACES_PREFIX=uploads
```

Generate secrets with:
```sh
./scripts/generate_secret.sh
```

### Installation

1. **Clone the repository:**
   ```sh
   git clone https://github.com/pedroanisio/clipdrop.git
   cd clipdrop
   ```

2. **Install dependencies:**
   ```sh
   pip install .
   # or with uv
   uv pip install .
   ```

3. **Run the application (default http://localhost:3000):**
   ```sh
   clipdrop
   # or
   python -m clipdrop
   ```

### Docker

Use the production compose file (runs gunicorn on port 3000, mapped to 3020 locally):
```sh
docker compose up --build
```

For development with the Flask server:
```sh
docker compose -f docker-compose.dev.yml up --build
```

### Usage

- Open `http://localhost:3000` (or `http://localhost:3020` when using Docker).
- Sign in with GitHub OAuth.
- Upload files via drag-and-drop or save clipboard text/images.
- View, download, or delete uploaded items.
- Favorite, tag, rename, and move clipboard items into folders.

### File Structure

```
clipdrop/
├── src/clipdrop/
│   ├── __init__.py
│   ├── __main__.py
│   ├── app.py
│   ├── crypto.py
│   ├── extensions.py
│   ├── models.py
│   ├── storage.py          # Storage abstraction (local/S3)
│   ├── static/
│   │   └── styles.css
│   └── templates/
│       ├── base.html
│       ├── index.html
│       ├── login.html
│       ├── clipboard_view.html
│       └── shared_clipboard.html
├── .do/
│   └── app.yaml            # Digital Ocean App Platform spec
├── tests/
├── docs/
├── scripts/
├── pyproject.toml
├── Dockerfile
└── docker-compose.yaml
```

## Deployment

### Digital Ocean App Platform

The project includes a ready-to-use App Platform specification in `.do/app.yaml`.

**Prerequisites:**
1. Create a Digital Ocean Space (for file storage)
2. Generate Spaces access keys (API > Spaces Keys)
3. Register a GitHub OAuth app with callback URL: `https://clipdrop.is42.io/login/github/authorized`

**Deploy:**
1. Push to GitHub
2. In DO Console: Apps > Create App > Select your repo
3. Configure environment variables (secrets) in the dashboard
4. Deploy

The app spec includes:
- Managed PostgreSQL database
- Spaces integration for persistent file storage
- Health checks
- Auto-deploy on push

### Contributing

Contributions are welcome! Please open an issue or submit a pull request for any changes or improvements.

### License

This project is licensed under the MIT License.

### Acknowledgements

- [Flask](https://flask.palletsprojects.com/)
- [APScheduler](https://apscheduler.readthedocs.io/)
- [Bootstrap](https://getbootstrap.com/)
