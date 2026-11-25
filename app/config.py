"""Ρυθμίσεις περιβάλλοντος (μόνο secrets / υποδομή). Όλα τα υπόλοιπα ρυθμίζονται από το dashboard."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    data_dir: Path
    secret_key: str
    admin_user: str
    admin_password_hash: str
    base_url: str
    cookie_secure: bool
    max_upload_mb: int
    ntfy_url_default: str
    ntfy_topic_default: str
    ntfy_token_default: str
    default_tz: str
    scheduler_enabled: bool

    @property
    def db_path(self) -> Path:
        return self.data_dir / "study.db"

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"

    @property
    def templates_dir(self) -> Path:
        return self.data_dir / "templates"


def load_config() -> Config:
    secret = os.environ.get("SECRET_KEY", "")
    if len(secret) < 32:
        raise RuntimeError("Το SECRET_KEY λείπει ή είναι μικρότερο από 32 χαρακτήρες (δες .env.example).")
    pw_hash = os.environ.get("ADMIN_PASSWORD_HASH", "")
    if not pw_hash.startswith("$2"):
        raise RuntimeError("Το ADMIN_PASSWORD_HASH λείπει ή δεν είναι bcrypt hash (python -m app.hashpw).")
    data_dir = Path(os.environ.get("DATA_DIR", "/data"))
    return Config(
        data_dir=data_dir,
        secret_key=secret,
        admin_user=os.environ.get("ADMIN_USER", "admin"),
        admin_password_hash=pw_hash,
        base_url=os.environ.get("APP_BASE_URL", "http://localhost:8000").rstrip("/"),
        cookie_secure=_bool("COOKIE_SECURE", True),
        max_upload_mb=int(os.environ.get("MAX_UPLOAD_MB", "50")),
        ntfy_url_default=os.environ.get("NTFY_URL", "http://ntfy").rstrip("/"),
        ntfy_topic_default=os.environ.get("NTFY_TOPIC", "study"),
        ntfy_token_default=os.environ.get("NTFY_TOKEN", ""),
        default_tz=os.environ.get("DEFAULT_TZ", "Europe/Athens"),
        scheduler_enabled=_bool("SCHEDULER_ENABLED", True),
    )


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def reset_config() -> None:
    global _config
    _config = None
