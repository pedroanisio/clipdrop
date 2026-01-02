"""Database models for authentication and clipboard storage."""

from datetime import datetime

from flask_login import UserMixin

from clipdrop.extensions import db


clipboard_item_tags = db.Table(
    "clipboard_item_tags",
    db.Column("item_id", db.Integer, db.ForeignKey("clipboard_item.id"), primary_key=True),
    db.Column("tag_id", db.Integer, db.ForeignKey("clipboard_tag.id"), primary_key=True),
)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    github_id = db.Column(db.String(64), unique=True, nullable=False)
    username = db.Column(db.String(255), nullable=False)
    avatar_url = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class OAuth(db.Model):
    __table_args__ = (db.UniqueConstraint("provider", "provider_user_id"),)

    id = db.Column(db.Integer, primary_key=True)
    provider = db.Column(db.String(64), nullable=False)
    provider_user_id = db.Column(db.String(256), nullable=False)
    token = db.Column(db.JSON, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    user = db.relationship(User)


class ClipboardFolder(db.Model):
    __table_args__ = (db.UniqueConstraint("user_id", "parent_id", "name"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    name = db.Column(db.String(255), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey("clipboard_folder.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    parent = db.relationship("ClipboardFolder", remote_side=[id], backref="children")


class ClipboardTag(db.Model):
    __table_args__ = (db.UniqueConstraint("user_id", "name"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    name = db.Column(db.String(64), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class ClipboardItem(db.Model):
    __table_args__ = (
        db.Index("ix_clipboard_item_user_folder", "user_id", "folder_id"),
        db.Index("ix_clipboard_item_user_favorite", "user_id", "favorite"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    folder_id = db.Column(db.Integer, db.ForeignKey("clipboard_folder.id"))
    name = db.Column(db.String(255), nullable=False)
    content = db.Column(db.LargeBinary, nullable=False)
    content_type = db.Column(db.String(255), nullable=False)
    is_text = db.Column(db.Boolean, default=True, nullable=False)
    size = db.Column(db.Integer, nullable=False)
    favorite = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    expires_at = db.Column(db.DateTime)

    folder = db.relationship("ClipboardFolder", backref="items")
    tags = db.relationship("ClipboardTag", secondary=clipboard_item_tags, backref="items")
    user = db.relationship(User)
