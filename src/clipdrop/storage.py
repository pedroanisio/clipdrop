"""
Storage abstraction layer for file operations.

Supports multiple backends:
- LocalStorage: Local filesystem (development)
- S3Storage: S3-compatible storage like Digital Ocean Spaces (production)
"""

import logging
import os
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import BinaryIO

logger = logging.getLogger(__name__)


class StorageFile:
    """Represents a file in storage with metadata."""

    def __init__(
        self,
        key: str,
        size: int,
        last_modified: datetime,
        content_type: str | None = None,
    ):
        self.key = key
        self.size = size
        self.last_modified = last_modified
        self.content_type = content_type

    @property
    def name(self) -> str:
        """Get filename from key."""
        return os.path.basename(self.key)


class StorageBackend(ABC):
    """Abstract base class for storage backends."""

    @abstractmethod
    def save(self, key: str, data: bytes, content_type: str | None = None) -> None:
        """Save data to storage."""
        pass

    @abstractmethod
    def read(self, key: str) -> bytes:
        """Read data from storage."""
        pass

    @abstractmethod
    def delete(self, key: str) -> bool:
        """Delete a file from storage. Returns True if deleted."""
        pass

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Check if a file exists."""
        pass

    @abstractmethod
    def list_files(self, prefix: str = "") -> list[StorageFile]:
        """List files with optional prefix filter."""
        pass

    @abstractmethod
    def get_file_info(self, key: str) -> StorageFile | None:
        """Get file metadata."""
        pass

    def delete_older_than(self, prefix: str, max_age: timedelta) -> list[str]:
        """Delete files older than max_age. Returns list of deleted keys."""
        deleted = []
        now = datetime.now(timezone.utc)
        for file in self.list_files(prefix):
            file_age = now - file.last_modified.replace(tzinfo=timezone.utc)
            if file_age > max_age:
                if self.delete(file.key):
                    deleted.append(file.key)
                    logger.info(f"Deleted expired file: {file.key}")
        return deleted


class LocalStorage(StorageBackend):
    """Local filesystem storage backend."""

    def __init__(self, base_path: str):
        self.base_path = base_path
        os.makedirs(base_path, exist_ok=True)

    def _full_path(self, key: str) -> str:
        """Get full filesystem path for a key."""
        return os.path.join(self.base_path, key)

    def save(self, key: str, data: bytes, content_type: str | None = None) -> None:
        """Save data to local filesystem."""
        path = self._full_path(key)
        os.makedirs(os.path.dirname(path) if os.path.dirname(key) else self.base_path, exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)

    def read(self, key: str) -> bytes:
        """Read data from local filesystem."""
        path = self._full_path(key)
        with open(path, "rb") as f:
            return f.read()

    def delete(self, key: str) -> bool:
        """Delete a file from local filesystem."""
        path = self._full_path(key)
        if os.path.exists(path):
            os.remove(path)
            return True
        return False

    def exists(self, key: str) -> bool:
        """Check if a file exists on local filesystem."""
        return os.path.exists(self._full_path(key))

    def list_files(self, prefix: str = "") -> list[StorageFile]:
        """List files in local directory."""
        files = []
        search_path = self._full_path(prefix) if prefix else self.base_path
        if not os.path.isdir(search_path):
            search_path = self.base_path

        for filename in os.listdir(search_path):
            if prefix and not filename.startswith(os.path.basename(prefix)):
                continue
            full_path = os.path.join(search_path, filename)
            if os.path.isfile(full_path):
                stat = os.stat(full_path)
                files.append(
                    StorageFile(
                        key=filename,
                        size=stat.st_size,
                        last_modified=datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc),
                    )
                )
        return files

    def get_file_info(self, key: str) -> StorageFile | None:
        """Get file metadata from local filesystem."""
        path = self._full_path(key)
        if not os.path.exists(path):
            return None
        stat = os.stat(path)
        return StorageFile(
            key=key,
            size=stat.st_size,
            last_modified=datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc),
        )


class S3Storage(StorageBackend):
    """S3-compatible storage backend (works with Digital Ocean Spaces, AWS S3, MinIO)."""

    def __init__(
        self,
        bucket: str,
        endpoint_url: str | None = None,
        region: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        prefix: str = "",
    ):
        try:
            import boto3
            from botocore.config import Config
        except ImportError:
            raise ImportError(
                "boto3 is required for S3 storage. Install it with: pip install boto3"
            )

        self.bucket = bucket
        self.prefix = prefix.strip("/")

        config = Config(
            signature_version="s3v4",
            retries={"max_attempts": 3, "mode": "standard"},
        )

        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=config,
        )

    def _full_key(self, key: str) -> str:
        """Get full S3 key with prefix."""
        if self.prefix:
            return f"{self.prefix}/{key}"
        return key

    def save(self, key: str, data: bytes, content_type: str | None = None) -> None:
        """Save data to S3."""
        extra_args = {}
        if content_type:
            extra_args["ContentType"] = content_type

        self.client.upload_fileobj(
            BytesIO(data),
            self.bucket,
            self._full_key(key),
            ExtraArgs=extra_args or None,
        )

    def read(self, key: str) -> bytes:
        """Read data from S3."""
        response = self.client.get_object(Bucket=self.bucket, Key=self._full_key(key))
        return response["Body"].read()

    def delete(self, key: str) -> bool:
        """Delete a file from S3."""
        try:
            self.client.delete_object(Bucket=self.bucket, Key=self._full_key(key))
            return True
        except Exception as e:
            logger.error(f"Failed to delete {key}: {e}")
            return False

    def exists(self, key: str) -> bool:
        """Check if a file exists in S3."""
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._full_key(key))
            return True
        except Exception:
            return False

    def list_files(self, prefix: str = "") -> list[StorageFile]:
        """List files in S3 bucket with optional prefix."""
        files = []
        search_prefix = self._full_key(prefix) if prefix else self.prefix

        paginator = self.client.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=self.bucket, Prefix=search_prefix)

        for page in pages:
            for obj in page.get("Contents", []):
                key = obj["Key"]
                # Remove prefix to get relative key
                if self.prefix and key.startswith(self.prefix + "/"):
                    key = key[len(self.prefix) + 1 :]
                files.append(
                    StorageFile(
                        key=key,
                        size=obj["Size"],
                        last_modified=obj["LastModified"],
                        content_type=obj.get("ContentType"),
                    )
                )
        return files

    def get_file_info(self, key: str) -> StorageFile | None:
        """Get file metadata from S3."""
        try:
            response = self.client.head_object(Bucket=self.bucket, Key=self._full_key(key))
            return StorageFile(
                key=key,
                size=response["ContentLength"],
                last_modified=response["LastModified"],
                content_type=response.get("ContentType"),
            )
        except Exception:
            return None


def create_storage_backend(storage_type: str | None = None, **kwargs) -> StorageBackend:
    """
    Factory function to create storage backend based on configuration.

    Args:
        storage_type: "local" or "s3". If None, auto-detect from environment.
        **kwargs: Backend-specific configuration options.

    Environment variables for auto-detection:
        STORAGE_TYPE: "local" or "s3"
        SPACES_BUCKET: Digital Ocean Spaces bucket name
        SPACES_ENDPOINT: Spaces endpoint URL (e.g., https://nyc3.digitaloceanspaces.com)
        SPACES_REGION: Spaces region (e.g., nyc3)
        SPACES_ACCESS_KEY: Access key ID
        SPACES_SECRET_KEY: Secret access key
        SPACES_PREFIX: Optional prefix for all keys (e.g., "uploads")
        UPLOAD_FOLDER: Local folder path (for local storage)
    """
    if storage_type is None:
        storage_type = os.getenv("STORAGE_TYPE", "local")

    if storage_type == "s3":
        bucket = kwargs.get("bucket") or os.getenv("SPACES_BUCKET")
        if not bucket:
            raise ValueError("S3 storage requires SPACES_BUCKET environment variable")

        return S3Storage(
            bucket=bucket,
            endpoint_url=kwargs.get("endpoint_url") or os.getenv("SPACES_ENDPOINT"),
            region=kwargs.get("region") or os.getenv("SPACES_REGION"),
            access_key=kwargs.get("access_key") or os.getenv("SPACES_ACCESS_KEY"),
            secret_key=kwargs.get("secret_key") or os.getenv("SPACES_SECRET_KEY"),
            prefix=kwargs.get("prefix") or os.getenv("SPACES_PREFIX", ""),
        )
    else:
        base_path = kwargs.get("base_path") or os.getenv("UPLOAD_FOLDER", "uploads")
        return LocalStorage(base_path=base_path)


# Global storage instance (initialized in create_app)
_storage: StorageBackend | None = None


def get_storage() -> StorageBackend:
    """Get the global storage backend instance."""
    if _storage is None:
        raise RuntimeError("Storage not initialized. Call init_storage() first.")
    return _storage


def init_storage(storage_type: str | None = None, **kwargs) -> StorageBackend:
    """Initialize the global storage backend."""
    global _storage
    _storage = create_storage_backend(storage_type, **kwargs)
    logger.info(f"Initialized storage backend: {type(_storage).__name__}")
    return _storage
