#!/usr/bin/env bash
# Backup βάσης, αρχείων και χρηστών ntfy στο ./backups (κρατά τα KEEP πιο πρόσφατα, default 14).
#   ./backup.sh
# Επαναφορά: δες README → Backup.
set -Eeuo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"
KEEP=${KEEP:-14}
OUT="$DIR/backups"
mkdir -p "$OUT"
DATA_DIR=$(grep -E '^STUDY_DATA_DIR=' .env 2>/dev/null | tail -n1 | cut -d= -f2- | sed -e "s/^'//" -e "s/'$//" || true)
stamp=$(date +%F-%H%M)
name="study-$stamp.tgz"

# 1) Συνεπές αντίγραφο της SQLite ενώ τρέχει η εφαρμογή
docker compose exec -T app python -m app.backup /data/backups/study-latest.db >/dev/null

# 2) Αρχειοθέτηση δεδομένων εφαρμογής + ntfy
if [ -n "$DATA_DIR" ]; then
  mounts=(-v "$DATA_DIR/app:/data:ro" -v "$DATA_DIR/ntfy:/ntfy:ro")
else
  mounts=(-v study_study-data:/data:ro -v study_ntfy-data:/ntfy:ro)
fi
docker run --rm "${mounts[@]}" -v "$OUT:/out" busybox:1.36 \
  sh -c "tar czf /out/$name -C / data ntfy && chown $(id -u):$(id -g) /out/$name && chmod 600 /out/$name"

# 3) Διατήρηση των KEEP πιο πρόσφατων
mapfile -t old < <(ls -1t "$OUT"/study-*.tgz 2>/dev/null | tail -n +"$((KEEP + 1))")
[ "${#old[@]}" -eq 0 ] || rm -f -- "${old[@]}"
echo "$(date '+%F %T') OK $OUT/$name"
