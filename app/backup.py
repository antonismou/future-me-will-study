"""Online backup της SQLite (ασφαλές ενώ τρέχει η εφαρμογή).

    docker compose exec app python -m app.backup            -> /data/backups/study-YYYYmmdd-HHMMSS.db
    docker compose exec app python -m app.backup /data/x.db
"""
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from .config import get_config


def main() -> None:
    cfg = get_config()
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else cfg.data_dir / "backups" / f"study-{datetime.now():%Y%m%d-%H%M%S}.db"
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(cfg.db_path)
    out = sqlite3.connect(dest)
    with out:
        src.backup(out)
    out.close()
    src.close()
    print(dest)


if __name__ == "__main__":
    main()
