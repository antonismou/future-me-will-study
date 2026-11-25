"""Αποθήκευση αρχείων που ανεβαίνουν από το dashboard."""
from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
from pathlib import Path

from fastapi import UploadFile

from .config import get_config

CATEGORIES = ["Ημερήσιες σημειώσεις", "Διαφάνειες", "Φροντιστήρια", "Λύσεις", "Παλιά θέματα", "Άλλο"]
MIME = {
    ".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".gif": "image/gif", ".txt": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
}
CHUNK = 1024 * 1024


class UploadRejected(ValueError):
    pass


def _check_magic(ext: str, head: bytes) -> bool:
    if ext == ".pdf":
        return head.startswith(b"%PDF")
    if ext in (".jpg", ".jpeg"):
        return head.startswith(b"\xff\xd8\xff")
    if ext == ".png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if ext == ".gif":
        return head.startswith(b"GIF8")
    if ext == ".webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    if ext in (".txt", ".md"):
        return b"\x00" not in head
    return False


def clean_name(name: str) -> str:
    name = Path(name.replace("\\", "/")).name.strip()
    name = "".join(ch for ch in name if ch.isprintable() and ch not in '<>:"|?*')
    return name[:200] or "αρχείο"


async def save_upload(conn: sqlite3.Connection, course_id: int, up: UploadFile, category: str, grp: str) -> tuple[int, bool]:
    """Επιστρέφει (file_id, νέο;). Πετά UploadRejected με ελληνικό μήνυμα."""
    cfg = get_config()
    name = clean_name(up.filename or "")
    ext = Path(name).suffix.lower()
    if ext not in MIME:
        raise UploadRejected(f"{name}: μη επιτρεπτός τύπος (μόνο PDF, εικόνες, txt/md)")
    if category not in CATEGORIES:
        category = "Άλλο"
    limit = cfg.max_upload_mb * 1024 * 1024
    tmp = cfg.data_dir / "tmp" / f"up-{secrets.token_hex(8)}"
    h = hashlib.sha256()
    size = 0
    head = b""
    try:
        with open(tmp, "wb") as out:
            while chunk := await up.read(CHUNK):
                if not head:
                    head = chunk[:16]
                size += len(chunk)
                if size > limit:
                    raise UploadRejected(f"{name}: ξεπερνά το όριο των {cfg.max_upload_mb} MB")
                h.update(chunk)
                out.write(chunk)
        if size == 0:
            raise UploadRejected(f"{name}: κενό αρχείο")
        if not _check_magic(ext, head):
            raise UploadRejected(f"{name}: το περιεχόμενο δεν αντιστοιχεί σε {ext}")
        digest = h.hexdigest()
        existing = conn.execute("SELECT id FROM file WHERE course_id=? AND sha256=?", (course_id, digest)).fetchone()
        if existing:
            return existing["id"], False
        rel = Path(str(course_id)) / digest[:2] / f"{digest}{ext}"
        dest = cfg.files_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp, dest)
        fid = conn.execute(
            "INSERT INTO file(course_id, category, grp, name, stored, mime, size, sha256) VALUES(?,?,?,?,?,?,?,?)",
            (course_id, category, grp.strip()[:120], name, rel.as_posix(), MIME[ext], size, digest)).lastrowid
        return fid, True
    finally:
        if tmp.exists():
            tmp.unlink()


def file_path(row: sqlite3.Row) -> Path:
    p = (get_config().files_dir / row["stored"]).resolve()
    if get_config().files_dir.resolve() not in p.parents:
        raise FileNotFoundError(row["stored"])
    return p


def delete_file(conn: sqlite3.Connection, row: sqlite3.Row) -> None:
    conn.execute("DELETE FROM file WHERE id=?", (row["id"],))
    try:
        file_path(row).unlink(missing_ok=True)
    except FileNotFoundError:
        pass
