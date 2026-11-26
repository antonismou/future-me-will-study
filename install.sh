#!/usr/bin/env bash
# Εγκατάσταση του future-me-will-study σε Linux server, κάτω από τον τρέχοντα χρήστη.
#
#   git clone <repo> ~/study && cd ~/study && ./install.sh
#   ./install.sh --check      (μόνο έλεγχος DNS, θυρών, HTTPS και λίστα εκκρεμοτήτων)
#
# Οδηγός βήμα-βήμα για Cloudflare (DNS) + Nginx Proxy Manager (reverse proxy με web UI):
# πρώτα ρωτά όλες τις ρυθμίσεις (με εξήγηση και παράδειγμα η καθεμία), μετά δείχνει κάθε χειροκίνητη
# ενέργεια (Cloudflare, router, NPM, κινητό) τη στιγμή που χρειάζεται και ελέγχει ότι έγινε.
# Μπορεί να ξανατρέξει με ασφάλεια (π.χ. μετά από update). Για αυτοματοποίηση, κάθε ερώτηση
# παίρνει τιμή από env var (STUDY_APP_DOMAIN, STUDY_CLOUDFLARE=dns|proxied, STUDY_ADMIN_PASSWORD, ...) και STUDY_ASSUME_YES=1.
set -Eeuo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"
ENV_FILE="$DIR/.env"
OVERRIDE="$DIR/docker-compose.override.yml"
TTY=${STUDY_TTY:-/dev/tty}   # STUDY_TTY: αρχείο απαντήσεων (μόνο για δοκιμές)

if [ -t 1 ]; then
  B=$'\e[1m'; D=$'\e[2m'; R=$'\e[31m'; G=$'\e[32m'; Y=$'\e[33m'; C=$'\e[36m'; N=$'\e[0m'
else
  B=; D=; R=; G=; Y=; C=; N=
fi
step() { printf '\n%s==> %s%s\n' "$B" "$*" "$N"; }
info() { printf '    %s\n' "$*"; }
ok()   { printf '    %s✓%s %s\n' "$G" "$N" "$*"; }
warn() { printf '    %s!%s %s\n' "$Y" "$N" "$*"; }
die()  { printf '\n%sΣφάλμα:%s %s\n' "$R" "$N" "$*" >&2; exit 1; }
trap 'die "η εντολή στη γραμμή $LINENO απέτυχε (δες το μήνυμα παραπάνω)."' ERR

# Διαδραστικό μόνο αν υπάρχει τερματικό και δεν ζητήθηκε αυτόματη λειτουργία.
INTERACTIVE=yes
if [ "${STUDY_ASSUME_YES-}" = 1 ] || ! { : <"$TTY"; } 2>/dev/null; then INTERACTIVE=no; fi
# Το τερματικό ανοίγει μία φορά (fd 3), ώστε όλες οι ερωτήσεις να διαβάζουν από την ίδια ροή.
if [ "$INTERACTIVE" = yes ]; then exec 3<"$TTY"; else exec 3</dev/null; fi

# ---------------------------------------------------------------- ερωτήσεις
# Κάθε μήνυμα τυπώνεται με printf (stdout) και το read διαβάζει σκέτο από το fd 3, ώστε η ερώτηση
# να φαίνεται πάντα πριν το script περιμένει απάντηση.

SETTINGS="STUDY_APP_DOMAIN STUDY_NTFY_DOMAIN STUDY_CLOUDFLARE STUDY_ADMIN_USER STUDY_ADMIN_PASSWORD
  STUDY_NTFY_TOPIC STUDY_PHONE_PASSWORD STUDY_TZ STUDY_MAX_UPLOAD_MB STUDY_DATA
  STUDY_NPM_CONTAINER STUDY_PROXY_NETWORK"
PRESET=" "   # όσες δόθηκαν από το περιβάλλον: δεν ξαναρωτιούνται ούτε στη διόρθωση
for v in $SETTINGS; do [ -z "${!v-}" ] || PRESET="$PRESET$v "; done

field() { # field "τίτλος" "γραμμή εξήγησης"...
  local t=$1 l; shift
  printf '\n    %s▌%s %s%s%s\n' "$C" "$N" "$B" "$t" "$N"
  for l in "$@"; do printf '      %s\n' "$l"; done
}

prompt() { printf '    %s➜%s %s ' "$C" "$N" "$*"; }

# Έλεγχοι τιμών: παίρνουν την απάντηση, θέτουν VALUE (καθαρισμένη τιμή) ή INVALID (τι φταίει).
v_any() { VALUE=$1; }
v_domain() {
  local d=${1,,}
  d=${d// /}; d=${d#*://}; d=${d%%/*}; d=${d%.}
  if [[ $d =~ ^([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}(:[0-9]+)?$ ]]; then VALUE=$d; return 0; fi
  INVALID="«$1» δεν είναι πλήρες domain. Γράψε το ολόκληρο, π.χ. study.example.com (χωρίς https://)."; return 1
}
v_ntfy_domain() {
  v_domain "$1" || return 1
  [ "$VALUE" != "$APP_DOMAIN" ] || { INVALID="Είναι ίδιο με της εφαρμογής. Χρειάζεται δεύτερο, διαφορετικό subdomain."; return 1; }
}
v_cloudflare() {
  case ${1,,} in
    1|dns) VALUE=dns ;;
    2|proxied) VALUE=proxied ;;
    *) INVALID="Γράψε 1 ή 2."; return 1 ;;
  esac
}
v_user() {
  [[ $1 =~ ^[A-Za-z0-9._@-]{1,64}$ ]] && { VALUE=$1; return 0; }
  INVALID="Μόνο λατινικά γράμματα, αριθμοί και . _ @ - (χωρίς κενά)."; return 1
}
v_topic() {
  [[ $1 =~ ^[A-Za-z0-9_-]{1,64}$ ]] && { VALUE=$1; return 0; }
  INVALID="Μόνο λατινικά γράμματα, αριθμοί, - και _ (χωρίς κενά, έως 64)."; return 1
}
v_tz() {
  if [[ $1 =~ ^[A-Za-z0-9_+/-]+$ ]] && { [ ! -d /usr/share/zoneinfo ] || [ -f "/usr/share/zoneinfo/$1" ]; }; then VALUE=$1; return 0; fi
  INVALID="Άγνωστη ζώνη ώρας. Μορφή Ήπειρος/Πόλη, π.χ. Europe/Athens ή Europe/Nicosia."; return 1
}
v_mb() {
  [[ $1 =~ ^[1-9][0-9]{0,3}$ ]] && { VALUE=$1; return 0; }
  INVALID="Γράψε έναν αριθμό από 1 έως 9999 (μόνο ψηφία, χωρίς «MB»)."; return 1
}
v_datadir() {
  [[ $1 != *[\'\":\$\#]* ]] && { VALUE=$1; return 0; }
  INVALID="Η διαδρομή δεν πρέπει να έχει εισαγωγικά, :, # ή \$."; return 1
}
v_npm() { # αριθμός από τη λίστα, όνομα container ή -
  if [[ $1 =~ ^[0-9]+$ ]] && [ "$1" -ge 1 ] && [ "$1" -le "${#CHOICES[@]}" ]; then VALUE=${CHOICES[$1 - 1]}; return 0; fi
  if [ "$1" = - ] || docker inspect "$1" >/dev/null 2>&1; then VALUE=$1; return 0; fi
  INVALID="Δεν υπάρχει container «$1». Γράψε αριθμό από τη λίστα, το ακριβές όνομα ή -"; return 1
}
v_network() { # αριθμός από τη λίστα ή όνομα δικτύου
  if [[ $1 =~ ^[0-9]+$ ]] && [ "$1" -ge 1 ] && [ "$1" -le "${#CHOICES[@]}" ]; then VALUE=${CHOICES[$1 - 1]}; return 0; fi
  if [[ $1 =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] && [[ ! $1 =~ ^([0-9]+|bridge|host|none)$ ]]; then VALUE=$1; return 0; fi
  INVALID="Χρειάζεται αριθμός από τη λίστα ή όνομα δικού σου δικτύου (όχι bridge/host/none)."; return 1
}

ask() { # ask VAR "τι να γράψεις" default validator   (πριν: field με την εξήγηση)
  local var=$1 label=$2 def=${3-} check=${4:-v_any} ans
  if [ -n "${!var-}" ]; then
    "$check" "${!var}" || die "Μη έγκυρη τιμή στη μεταβλητή $var: $INVALID"
    printf -v "$var" '%s' "$VALUE"; info "$label: ${B}$VALUE${N}"; return 0
  fi
  while :; do
    ans=""
    if [ -n "$def" ]; then prompt "$label ${D}[Enter = $def]${N}:"; else prompt "$label:"; fi
    if ! read -r ans <&3; then
      echo
      [ -n "$def" ] || die "Χρειάζεται τερματικό για την ερώτηση «$label» (ή όρισε τη μεταβλητή $var)."
    fi
    ans=${ans%$'\r'}; ans=${ans:-$def}
    if [ -z "$ans" ]; then warn "Δεν έγραψες τίποτα. Αυτό το πεδίο είναι υποχρεωτικό."; continue; fi
    if "$check" "$ans"; then break; fi
    [ "$INTERACTIVE" = yes ] || die "$var: $INVALID"
    warn "$INVALID"
  done
  printf -v "$var" '%s' "$VALUE"
}

ask_secret() { # ask_secret VAR "τι να γράψεις" minlen allow_random(yes/no)
  local var=$1 label=$2 min=$3 rnd=$4 a b gen="${1}_GENERATED" prev="PREV_$1" hint=""
  if [ "${!var-}" = random ] && [ "$rnd" = yes ]; then
    printf -v "$var" '%s' "$(randstr 20)"; printf -v "$gen" '%s' yes; return 0
  fi
  if [ -n "${!var-}" ]; then
    a=${!var}
    [ ${#a} -ge "$min" ] || die "Η τιμή της $var θέλει τουλάχιστον $min χαρακτήρες."
    info "$label: ${D}(δόθηκε από τη μεταβλητή $var)${N}"; return 0
  fi
  [ "$INTERACTIVE" = yes ] || die "Χρειάζεται τερματικό για την ερώτηση «$label» (ή όρισε τη μεταβλητή $var)."
  if [ -n "${!prev-}" ]; then hint=" ${D}[Enter = αυτός που έδωσες πριν]${N}"
  elif [ "$rnd" = yes ]; then hint=" ${D}[Enter = να φτιαχτεί τυχαίος]${N}"; fi
  while :; do
    a=""; b=""
    prompt "$label$hint:"; read -r -s a <&3 || die "Διακόπηκε η εισαγωγή."; echo
    a=${a%$'\r'}
    if [ -z "$a" ] && [ -n "${!prev-}" ]; then a=${!prev}; break; fi
    if [ -z "$a" ] && [ "$rnd" = yes ]; then
      a=$(randstr 20); printf -v "$gen" '%s' yes; ok "Θα φτιαχτεί τυχαίος. Θα τον δεις στο βήμα του κινητού."; break
    fi
    if [ ${#a} -lt "$min" ]; then warn "Έγραψες ${#a} χαρακτήρες, χρειάζονται τουλάχιστον $min."; continue; fi
    prompt "Γράψε τον ίδιο κωδικό ξανά, για επιβεβαίωση:"; read -r -s b <&3 || die "Διακόπηκε η εισαγωγή."; echo
    b=${b%$'\r'}
    if [ "$a" = "$b" ]; then printf -v "$gen" '%s' ""; ok "Ο κωδικός καταχωρήθηκε."; break; fi
    warn "Οι δύο κωδικοί δεν ταιριάζουν, ξαναγράψε τους."
  done
  printf -v "$var" '%s' "$a"
}

confirm() { # confirm "ερώτηση" y|n
  local q=$1 def=${2:-y} ans hint
  if [ "$INTERACTIVE" = no ]; then [ "$def" = y ]; return; fi
  [ "$def" = y ] && hint="Enter = ναι" || hint="Enter = όχι"
  while :; do
    ans=""
    prompt "$q ${D}[ν = ναι / ο = όχι · $hint]${N}:"
    read -r ans <&3 || { echo; ans=$def; }
    ans=${ans%$'\r'}; ans=${ans:-$def}
    case $ans in
      y*|Y*|ν*|Ν*) return 0 ;;
      n*|N*|o*|O*|ο*|Ο*|ό*|Ό*) return 1 ;;
    esac
    warn "Γράψε ν (ναι) ή ο (όχι) και πάτα Enter."
  done
}

pause() { # pause "τι να κάνεις πριν πατήσεις Enter"
  [ "$INTERACTIVE" = yes ] || return 0
  local _
  prompt "$1"
  read -r _ <&3 || echo
}

todo() { printf '\n    %s➜ ΚΑΝΕ ΤΩΡΑ (έξω από τον installer):%s %s%s%s\n' "$C" "$N" "$B" "$*" "$N"; }
line() { printf '      %s\n' "$*"; }

# guide "περιγραφή" check_fn instructions_fn
# Αν ο έλεγχος περνά ήδη, δεν δείχνει τίποτα. Αλλιώς δείχνει οδηγίες και περιμένει.
guide() {
  local what=$1 check=$2 instr=$3 ans
  CHECK_DETAIL=""; CHECK_HINT=""
  if "$check"; then ok "$what ${D}($CHECK_DETAIL)${N}"; return 0; fi
  "$instr"
  if [ "$INTERACTIVE" = no ]; then warn "$what: εκκρεμεί ($CHECK_DETAIL)."; return 1; fi
  printf '\n    %sΌταν τελειώσεις τα παραπάνω, γύρνα εδώ και διάλεξε:%s\n' "$B" "$N"
  line "Enter            έλεγχος ότι έγινε"
  line "?  και Enter     δείξε ξανά τις οδηγίες"
  line "s  και Enter     προχώρα, θα το κάνω αργότερα"
  while :; do
    prompt "Η επιλογή σου (Enter / ? / s):"
    read -r ans <&3 || { echo; ans=s; }
    case ${ans%$'\r'} in
      s|S|σ|Σ) warn "$what: το αφήνεις για αργότερα (έλεγχος: ./install.sh --check)."; return 1 ;;
      '?'|';') "$instr"; continue ;;
    esac
    info "${D}Ελέγχω...${N}"
    CHECK_HINT=""
    if "$check"; then ok "$what ${D}($CHECK_DETAIL)${N}"; return 0; fi
    warn "Όχι ακόμα: $CHECK_DETAIL."
    [ -z "$CHECK_HINT" ] || line "${Y}Τι να κοιτάξεις:${N} $CHECK_HINT"
  done
}

randstr() { local s; s=$(head -c 120 /dev/urandom | base64 | tr -dc 'A-Za-z0-9'); printf '%s' "${s:0:$1}"; }

env_get() { [ -f "$ENV_FILE" ] || return 0; grep -E "^$1=" "$ENV_FILE" | tail -n1 | cut -d= -f2- | sed -e "s/^'//" -e "s/'$//" || true; }

env_set() { # env_set KEY VALUE  (VALUE γράφεται σε μονά εισαγωγικά)
  local key=$1 val=$2 tmp
  tmp=$(mktemp "$DIR/.env.XXXXXX")
  { grep -vE "^$key=" "$ENV_FILE" || true; printf "%s='%s'\n" "$key" "$val"; } >"$tmp"
  chmod 600 "$tmp"; mv "$tmp" "$ENV_FILE"
}

wait_healthy() { # wait_healthy container
  local c=$1 s i
  for i in $(seq 1 90); do
    s=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$c" 2>/dev/null || echo missing)
    case $s in healthy) ok "$c: healthy"; return 0 ;; unhealthy|exited|dead) break ;; esac
    sleep 2
  done
  docker logs --tail 40 "$c" >&2 || true
  die "Το $c δεν έγινε healthy (κατάσταση: $s)."
}

ntfy_user_exists() { local out; out=$(docker compose exec -T ntfy ntfy user list 2>&1 || true); grep -qE "^user $1 " <<<"$out"; }
networks_of() { docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$1"; }

send_test_notification() { # send_test_notification "τίτλος"
  docker compose exec -T -e T="$1" app python - <<'PY' >/dev/null 2>&1
import os
from app.db import connect
from app.settings_store import get_settings
from app import notify
s = get_settings(connect())
notify.publish(s, {"title": os.environ["T"], "message": "Αν βλέπεις αυτό το μήνυμα, οι ειδοποιήσεις φτάνουν στο κινητό.",
                   "tags": ["white_check_mark"]})
PY
}

# ---------------------------------------------------------------- δίκτυο, DNS, HTTPS

is_private_ip() { [[ $1 =~ ^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.|127\.|100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.) ]]; }

http_get() { # http_get URL -> σώμα απάντησης (κενό αν αποτύχει)
  if command -v curl >/dev/null; then curl -fsS --max-time 8 "$1" 2>/dev/null || true
  elif command -v wget >/dev/null; then wget -qO- -T 8 "$1" 2>/dev/null || true
  fi
}

detect_ips() {
  local ipre='^[0-9]+(\.[0-9]+){3}$'
  PUBLIC_IP=${STUDY_PUBLIC_IP-}
  if [ -z "$PUBLIC_IP" ]; then
    PUBLIC_IP=$(http_get https://api.ipify.org)
    [[ $PUBLIC_IP =~ $ipre ]] || PUBLIC_IP=$(http_get https://ifconfig.me/ip)
    [[ $PUBLIC_IP =~ $ipre ]] || PUBLIC_IP=""
  fi
  LOCAL_IP=$( (ip -4 route get 1.1.1.1 2>/dev/null || true) | awk '{for (i = 1; i <= NF; i++) if ($i == "src") { print $(i + 1); exit }}')
  BEHIND_NAT=no
  if [ -n "$LOCAL_IP" ] && is_private_ip "$LOCAL_IP"; then BEHIND_NAT=yes; fi
}

resolve() { (getent ahostsv4 "$1" 2>/dev/null || true) | awk 'NR == 1 { print $1 }'; }

dns_state() { # dns_state resolved_ip
  if [ -z "$1" ]; then echo fail
  elif [ "$CF_MODE" != proxied ] && [ -n "$PUBLIC_IP" ] && [ "$1" != "$PUBLIC_IP" ]; then echo warn
  else echo ok; fi
}

port_listening() { local out; out=$( (ss -ltnH 2>/dev/null || netstat -ltn 2>/dev/null) || true); grep -qE "[:.]$1[[:space:]]" <<<"$out"; }

container_health() { docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$1" 2>/dev/null || echo "δεν υπάρχει"; }

# --- έλεγχοι για τον οδηγό (θέτουν CHECK_DETAIL και CHECK_HINT)
check_dns_for() { # check_dns_for host
  local host=${1%%:*} r
  r=$(resolve "$host")
  if [ -z "$r" ]; then
    CHECK_DETAIL="το $host δεν επιλύεται ακόμα"
    CHECK_HINT="ότι αποθήκευσες το record στο Cloudflare με σωστό Name. Η αλλαγή θέλει 1-5 λεπτά."
    return 1
  fi
  # Με proxy (πορτοκαλί σύννεφο) το DNS δείχνει IP του Cloudflare· την πραγματική διαδρομή την ελέγχει το HTTPS.
  if [ "$CF_MODE" != proxied ] && [ -n "$PUBLIC_IP" ] && [ "$r" != "$PUBLIC_IP" ]; then
    CHECK_DETAIL="το $host δείχνει στο $r αντί για $PUBLIC_IP"
    CHECK_HINT="στο record: IPv4 = $PUBLIC_IP και Proxy status = «DNS only» (γκρι σύννεφο)."
    return 1
  fi
  CHECK_DETAIL="$host -> $r"; return 0
}
check_dns_both() {
  local first
  check_dns_for "$APP_DOMAIN" || return 1
  first=$CHECK_DETAIL
  check_dns_for "$NTFY_DOMAIN" || return 1
  CHECK_DETAIL="$first · $CHECK_DETAIL"
}

probe() { # probe URL -> HTTP_BODY, HTTP_CODE, HTTP_RC (κωδικός εξόδου του curl)
  local out
  HTTP_BODY=""; HTTP_CODE=000; HTTP_RC=0
  if command -v curl >/dev/null; then
    if out=$(curl -sS --max-time 8 -w '\n%{http_code}' "$1" 2>/dev/null); then HTTP_RC=0; else HTTP_RC=$?; fi
    HTTP_CODE=${out##*$'\n'}; HTTP_BODY=${out%$'\n'*}
  else
    HTTP_BODY=$(http_get "$1"); if [ -n "$HTTP_BODY" ]; then HTTP_CODE=200; else HTTP_RC=1; fi
  fi
}

explain_https() { # explain_https forward_host forward_port -> CHECK_HINT
  local fh=$1 fp=$2
  case $HTTP_RC in
    6)  CHECK_HINT="το DNS δεν επιλύεται από τον server. Έλεγξε το record στο Cloudflare." ;;
    7)  CHECK_HINT="η σύνδεση στη θύρα 443 απορρίφθηκε. Τρέχει ο NPM; Είναι σωστό το port forwarding;" ;;
    28) CHECK_HINT="έληξε ο χρόνος αναμονής. Έλεγξε port forwarding / firewall για τις 80 και 443." ;;
    35) CHECK_HINT="απέτυχε το TLS: συνήθως δεν έχει βγει ακόμα πιστοποιητικό (NPM -> Proxy Host -> SSL)." ;;
    47) CHECK_HINT="ατέρμονη ανακατεύθυνση: στο Cloudflare βάλε SSL/TLS = «Full (strict)»." ;;
    51|60) CHECK_HINT="μη έγκυρο πιστοποιητικό: στο NPM, καρτέλα SSL, ζήτα Let's Encrypt για αυτό το domain." ;;
    *)
      case $HTTP_CODE in
        502|504) CHECK_HINT="ο NPM δεν φτάνει στο container. Scheme = http, Forward Hostname = $fh, Port = $fp, και ο NPM στο δίκτυο $PROXY_NETWORK." ;;
        301|302|307|308) CHECK_HINT="ανακατεύθυνση: στο Cloudflare βάλε SSL/TLS = «Full (strict)»." ;;
        403) CHECK_HINT="το αίτημα μπλοκαρίστηκε: έλεγξε Cloudflare Security (Bot Fight Mode, WAF) και Access List στο NPM." ;;
        200|404) CHECK_HINT="απαντά άλλη σελίδα: λείπει ή έχει λάθος Domain Names το Proxy Host στο NPM." ;;
        521|522|523) CHECK_HINT="το Cloudflare δεν φτάνει στον server σου: δημόσια IP στο record και ανοιχτή η 443." ;;
        525|526) CHECK_HINT="το Cloudflare δεν δέχεται το πιστοποιητικό του NPM: ζήτα Let's Encrypt στην καρτέλα SSL." ;;
        *) CHECK_HINT="το Proxy Host στο NPM και το record στο Cloudflare." ;;
      esac ;;
  esac
  if [ "${BEHIND_NAT-}" = yes ] && { [ "$HTTP_RC" = 7 ] || [ "$HTTP_RC" = 28 ]; }; then
    CHECK_HINT="$CHECK_HINT Κάποια router δεν επιτρέπουν έλεγχο από μέσα: άνοιξε τη διεύθυνση από το κινητό με δεδομένα· αν ανοίγει, γράψε s."
  fi
}

check_https() { # check_https url body_pattern forward_host forward_port
  probe "$1"
  # shellcheck disable=SC2053
  if [[ $HTTP_BODY == $2 ]]; then CHECK_DETAIL="$1 απαντά"; return 0; fi
  if [ "$HTTP_RC" = 0 ]; then CHECK_DETAIL="το $1 απάντησε HTTP $HTTP_CODE αλλά όχι σωστά"
  else CHECK_DETAIL="το $1 δεν απαντά ακόμα"; fi
  explain_https "$3" "$4"; return 1
}
check_https_app()  { check_https "https://$APP_DOMAIN/healthz" '*"ok":true*' study-app 8000; }
check_https_ntfy() { check_https "https://$NTFY_DOMAIN/v1/health" '*healthy*true*' study-ntfy 80; }

# --- οδηγίες τη στιγμή που χρειάζονται
cf_cloud() { if [ "$CF_MODE" = proxied ]; then echo "Proxied (πορτοκαλί)"; else echo "DNS only (γκρι)"; fi; }

dns_instructions() {
  local host ip=${PUBLIC_IP:-"<η δημόσια IP του server: τρέξε εκεί  curl https://ifconfig.me>"} n=0
  todo "Cloudflare: δύο DNS records"
  line "1. Άνοιξε ${B}https://dash.cloudflare.com${N} και συνδέσου."
  line "2. Διάλεξε το domain σου -> αριστερό μενού ${B}DNS -> Records${N}."
  line "3. Πάτα ${B}Add record${N}, συμπλήρωσε όπως παρακάτω και πάτα ${B}Save${N} (μία φορά για κάθε record):"
  for host in "${APP_DOMAIN%%:*}" "${NTFY_DOMAIN%%:*}"; do
    n=$((n + 1))
    line ""
    line "   Record $n"
    line "     Type:          ${B}A${N}"
    line "     Name:          ${B}$host${N}   ${D}(το Cloudflare ίσως δείξει μόνο «${host%%.*}», είναι σωστό)${N}"
    line "     IPv4 address:  ${B}$ip${N}"
    line "     Proxy status:  ${B}$(cf_cloud)${N}"
    line "     TTL:           Auto"
  done
  line ""
  line "Αν υπάρχει ήδη record με το ίδιο Name, πάτα ${B}Edit${N} σε αυτό αντί να φτιάξεις δεύτερο."
  if [ "$CF_MODE" = proxied ]; then
    line ""
    line "4. Αριστερό μενού ${B}SSL/TLS -> Overview -> Configure${N}: διάλεξε ${B}Full (strict)${N}."
    line "   ${Y}Με «Flexible» η σελίδα κάνει ατέρμονη ανακατεύθυνση.${N} ${D}(Η ρύθμιση ισχύει για όλο το domain.)${N}"
  fi
}

ports_instructions() {
  todo "Άνοιξε τις θύρες 80 και 443 προς τον server"
  if [ "$BEHIND_NAT" = yes ]; then
    line "Ο server έχει ιδιωτική IP (${B}$LOCAL_IP${N}), άρα είναι πίσω από router."
    line "1. Άνοιξε τη σελίδα του router (συνήθως http://192.168.1.1) και συνδέσου."
    line "2. Βρες το μενού ${B}Port Forwarding${N} (ή NAT / Virtual Server) και πρόσθεσε δύο κανόνες:"
    line "     TCP, εξωτερική θύρα ${B}80${N}   ->  ${B}$LOCAL_IP${N}, εσωτερική θύρα ${B}80${N}"
    line "     TCP, εξωτερική θύρα ${B}443${N}  ->  ${B}$LOCAL_IP${N}, εσωτερική θύρα ${B}443${N}"
    line "3. Στο router, κλείδωσε την IP $LOCAL_IP για τον server (DHCP reservation)."
    line ""
    line "${D}Αν η δημόσια IP σου αλλάζει, χρειάζεσαι Dynamic DNS που ενημερώνει το Cloudflare${N}"
    line "${D}(π.χ. container favonia/cloudflare-ddns ή η λειτουργία DDNS του router).${N}"
    line "${D}Αν η WAN IP στο router διαφέρει από τη δημόσια (${PUBLIC_IP:-?}), είσαι πίσω από CGNAT:${N}"
    line "${D}ζήτα δημόσια IP από τον πάροχο internet.${N}"
  else
    line "Στο firewall του παρόχου (π.χ. Hetzner Firewall, Oracle Security List, AWS Security Group):"
    line "  επίτρεψε εισερχόμενα ${B}TCP 80${N} και ${B}TCP 443${N} από παντού."
    line "Αν χρησιμοποιείς ufw σε αυτόν τον server, τρέξε σε άλλο τερματικό:  ${B}sudo ufw allow 80,443/tcp${N}"
  fi
  line "${D}Η θύρα 81 (διαχείριση του NPM) να ΜΗΝ είναι ανοιχτή προς το internet.${N}"
  if port_listening 80 && port_listening 443; then
    line "${G}✓${N} Σε αυτόν τον server κάτι ακούει ήδη στις 80/443 (ο Nginx Proxy Manager)."
  else
    line "${R}✗${N} Τίποτα δεν ακούει στις 80/443 εδώ. Βεβαιώσου ότι τρέχει ο Nginx Proxy Manager."
  fi
}

npm_open() {
  line "Άνοιξε στον browser το web UI του NPM: ${B}http://${LOCAL_IP:-<IP-του-server>}:81${N} και συνδέσου."
  line "Πάνω μενού ${B}Hosts -> Proxy Hosts${N} -> κουμπί ${B}Add Proxy Host${N}."
}

npm_ssl() { # κοινή καρτέλα SSL και των δύο proxy hosts
  line "Καρτέλα ${B}SSL${N}"
  line "  SSL Certificate:           ${B}Request a new SSL Certificate${N}"
  line "  Force SSL                  ✓"
  line "  HTTP/2 Support             ✓"
  line "  HSTS Enabled               ✓"
  if [ "$CF_MODE" = proxied ]; then
    line "  Use a DNS Challenge        ✓"
    line "  DNS Provider:              ${B}Cloudflare${N}"
    line "  Credentials File Content:  ${B}dns_cloudflare_api_token=ΤΟ_TOKEN_ΣΟΥ${N}"
    line "     (σβήσε το παράδειγμα που έχει μέσα και βάλε μόνο αυτή τη γραμμή)"
  fi
  line "  Email (αν το ζητήσει):     το email σου, για το Let's Encrypt"
  line "  I Agree to the Let's Encrypt Terms  ✓"
}

cf_token_help() {
  line "${B}Πρώτα, ένα API token του Cloudflare${N} (μία φορά, το ίδιο και για τα δύο Proxy Hosts):"
  line "  https://dash.cloudflare.com/profile/api-tokens -> ${B}Create Token${N}"
  line "  -> στο template ${B}Edit zone DNS${N} πάτα ${B}Use template${N}"
  line "  -> Zone Resources: Include · Specific zone · το domain σου"
  line "  -> Continue to summary -> Create Token -> αντίγραψέ το (εμφανίζεται μόνο μία φορά)."
  line "  ${D}Το token μπαίνει μόνο στον NPM, όχι σε αυτόν τον installer.${N}"
  line ""
}

npm_cert_trouble() {
  line ""
  if [ "$CF_MODE" = proxied ]; then
    line "${D}Αν το πιστοποιητικό αποτύχει: έλεγξε το token (σωστό domain, χωρίς κενά) και ξαναπάτα Save.${N}"
  else
    line "${D}Αν το πιστοποιητικό αποτύχει («Internal Error»): το record να είναι «DNS only», να δείχνει στον server${N}"
    line "${D}και η 80 να είναι ανοιχτή. Εναλλακτικά τσέκαρε «Use a DNS Challenge» -> Cloudflare και βάλε${N}"
    line "${D}dns_cloudflare_api_token=... (token: dash.cloudflare.com/profile/api-tokens, template «Edit zone DNS»).${N}"
  fi
}

app_nginx_conf() { printf 'client_max_body_size %sm;\nproxy_read_timeout 120s;\n' "$((${MAX_MB:-50} + 10))"; }
ntfy_nginx_conf() { printf 'proxy_buffering off;\nproxy_request_buffering off;\nproxy_read_timeout 3m;\nproxy_send_timeout 3m;\n'; }

npm_instructions_app() {
  todo "Nginx Proxy Manager: Proxy Host για την εφαρμογή"
  [ "$CF_MODE" != proxied ] || cf_token_help
  npm_open
  line ""
  line "Καρτέλα ${B}Details${N}"
  line "  Domain Names:              ${B}${APP_DOMAIN%%:*}${N}   ${D}(γράψ' το και πάτα Enter)${N}"
  line "  Scheme:                    ${B}http${N}"
  line "  Forward Hostname / IP:     ${B}study-app${N}"
  line "  Forward Port:              ${B}8000${N}"
  line "  Block Common Exploits      ✓"
  npm_ssl
  line "Καρτέλα ${B}Advanced${N} (σε νεότερες εκδόσεις: εικονίδιο ⚙) -> Custom Nginx Configuration, επικόλλησε:"
  printf '\n%s\n\n' "$(app_nginx_conf)"
  line "Πάτα ${B}Save${N}."
  npm_cert_trouble
}

npm_instructions_ntfy() {
  todo "Nginx Proxy Manager: Proxy Host για το ntfy (ειδοποιήσεις)"
  npm_open
  line ""
  line "Καρτέλα ${B}Details${N}"
  line "  Domain Names:              ${B}${NTFY_DOMAIN%%:*}${N}   ${D}(γράψ' το και πάτα Enter)${N}"
  line "  Scheme:                    ${B}http${N}"
  line "  Forward Hostname / IP:     ${B}study-ntfy${N}"
  line "  Forward Port:              ${B}80${N}"
  line "  Block Common Exploits      ✓"
  line "  Websockets Support         ✓   ${Y}(απαραίτητο για τις ειδοποιήσεις)${N}"
  npm_ssl
  line "Καρτέλα ${B}Advanced${N} (ή ⚙) -> Custom Nginx Configuration, επικόλλησε:"
  printf '\n%s\n\n' "$(ntfy_nginx_conf)"
  line "Πάτα ${B}Save${N}."
  npm_cert_trouble
}

phone_instructions() {
  todo "Σύνδεσε το κινητό (Android)"
  line "1. Εγκατάστησε την εφαρμογή ${B}ntfy${N} από Google Play ή F-Droid."
  line "2. Στην εφαρμογή: ${B}Settings -> Manage users -> Add user${N}"
  line "     Service URL:  ${B}https://${NTFY_DOMAIN}${N}"
  line "     Username:     ${B}phone${N}"
  if [ "${STUDY_PHONE_PASSWORD_GENERATED-}" = yes ]; then
    line "     Password:     ${B}$STUDY_PHONE_PASSWORD${N}   ${Y}<- σημείωσέ τον, δεν αποθηκεύεται πουθενά${N}"
  else
    line "     Password:     ο κωδικός που έδωσες για το κινητό"
  fi
  line "3. Πίσω στην αρχική οθόνη: ${B}+${N} (Subscribe to topic)"
  line "     Topic name:   ${B}$TOPIC${N}"
  line "     ✓ Use another server:  ${B}https://${NTFY_DOMAIN}${N}"
  line "     -> Subscribe"
  line "4. Ρυθμίσεις Android -> Εφαρμογές -> ntfy -> Μπαταρία -> ${B}Χωρίς περιορισμούς${N}"
  line "   ${D}(αλλιώς οι ειδοποιήσεις μπορεί να αργούν όταν το κινητό είναι σε αδράνεια)${N}"
}

phone_troubleshooting() {
  info "Τι να ελέγξεις:"
  line "- Στο κινητό, ο server είναι ακριβώς https://${NTFY_DOMAIN} (με https, χωρίς / στο τέλος)."
  line "- Χρήστης ${B}phone${N} και σωστός κωδικός (Settings -> Manage users)."
  line "- Το topic είναι ακριβώς ${B}$TOPIC${N} (πεζά/κεφαλαία μετράνε)."
  line "- Στο NPM, στο Proxy Host του ntfy, είναι ενεργό το ${B}Websockets Support${N}."
  line "- Logs:  docker compose logs --tail 50 ntfy"
}

# ---------------------------------------------------------------- λίστα εκκρεμοτήτων (--check / τέλος)

mark() {
  case $1 in
    ok) printf '%s✓%s' "$G" "$N" ;;
    warn|unknown) printf '%s?%s' "$Y" "$N" ;;
    *) printf '%s✗%s' "$R" "$N" ;;
  esac
}

pad() { printf '%s%*s' "$1" $(( $2 > ${#1} ? $2 - ${#1} : 1 )) ''; }

run_checks() {
  detect_ips
  DNS_APP=$(resolve "${APP_DOMAIN%%:*}"); DNS_NTFY=$(resolve "${NTFY_DOMAIN%%:*}")
  S_DNS_APP=$(dns_state "$DNS_APP"); S_DNS_NTFY=$(dns_state "$DNS_NTFY")
  if [ "$S_DNS_APP" = ok ] && [ "$S_DNS_NTFY" = ok ]; then S_DNS=ok
  elif [ "$S_DNS_APP" = fail ] || [ "$S_DNS_NTFY" = fail ]; then S_DNS=fail
  else S_DNS=warn; fi
  if port_listening 80 && port_listening 443; then S_PORTS=ok; else S_PORTS=fail; fi
  H_APP=$(container_health study-app); H_NTFY=$(container_health study-ntfy)
  if [ "$H_APP" = healthy ] && [ "$H_NTFY" = healthy ]; then S_CONT=ok; else S_CONT=fail; fi
  if check_https_app; then S_HTTPS_APP=ok; else S_HTTPS_APP=fail; fi
  if check_https_ntfy; then S_HTTPS_NTFY=ok; else S_HTTPS_NTFY=fail; fi
  if [ "$S_HTTPS_APP" = ok ] && [ "$S_HTTPS_NTFY" = ok ]; then S_NPM=ok; else S_NPM=fail; fi
  if [ "$S_CONT" = ok ] && [ "$S_DNS" = ok ] && [ "$S_NPM" = ok ]; then ALL_OK=yes; else ALL_OK=no; fi
}

print_next_steps() {
  local ip=${PUBLIC_IP:-"<δημόσια IP του server>"} app_host=${APP_DOMAIN%%:*} ntfy_host=${NTFY_DOMAIN%%:*}
  local ssl="SSL: Request a new SSL Certificate · Force SSL · HTTP/2 · HSTS"
  local cf_extra=""
  if [ "$CF_MODE" = proxied ]; then
    ssl="$ssl · DNS Challenge (Cloudflare API token)"
    cf_extra=$'
     Και: SSL/TLS -> Overview -> Full (strict)'
  fi
  cat <<EOF

${B}==================================================================${N}
${B}  Κατάσταση εγκατάστασης${N}
${B}==================================================================${N}
  ${D}✓ εντάξει · ✗ εκκρεμεί · ? θέλει προσοχή / δεν ελέγχεται από εδώ${N}

$(mark "$S_CONT") ${B}0. Containers${N}   app: $H_APP · ntfy: $H_NTFY

$(mark "$S_DNS") ${B}1. Cloudflare -> DNS -> Records${N}  (https://dash.cloudflare.com)
     Type  $(pad Name 28)$(pad "IPv4 address" 18)Proxy status
     A     $(pad "$app_host" 28)$(pad "$ip" 18)$(cf_cloud)
     A     $(pad "$ntfy_host" 28)$(pad "$ip" 18)$(cf_cloud)${cf_extra}
     Τώρα:  $(mark "$S_DNS_APP") $app_host -> ${DNS_APP:-δεν υπάρχει ακόμα}
            $(mark "$S_DNS_NTFY") $ntfy_host -> ${DNS_NTFY:-δεν υπάρχει ακόμα}
EOF
  printf '\n%s %s2. Θύρες 80 και 443 προς τον server%s\n' "$(mark "$S_PORTS")" "$B" "$N"
  if [ "$BEHIND_NAT" = yes ]; then
    printf '     Router -> Port forwarding: TCP 80 και 443 -> %s\n' "$LOCAL_IP"
  else
    printf '     Firewall παρόχου: επίτρεψε TCP 80 και 443 (ufw: sudo ufw allow 80,443/tcp)\n'
  fi
  cat <<EOF

$(mark "$S_NPM") ${B}3. Nginx Proxy Manager (http://${LOCAL_IP:-<IP-server>}:81) -> Hosts -> Proxy Hosts${N}
     $app_host -> Scheme http · Forward study-app · Port 8000
        Advanced: $(app_nginx_conf | tr '\n' ' ')
     $ntfy_host -> Scheme http · Forward study-ntfy · Port 80 · Websockets Support ✓
        Advanced: $(ntfy_nginx_conf | tr '\n' ' ')
     $ssl
     Ο NPM πρέπει να είναι στο docker network: $PROXY_NETWORK
     Τώρα:  $(mark "$S_HTTPS_APP") https://$APP_DOMAIN/healthz
            $(mark "$S_HTTPS_NTFY") https://$NTFY_DOMAIN/v1/health

$(mark unknown) ${B}4. Κινητό${N}  εφαρμογή ntfy -> server https://$NTFY_DOMAIN · χρήστης phone · topic $TOPIC

$(mark unknown) ${B}5. Dashboard${N}  https://$APP_DOMAIN -> Μαθήματα -> Νέο μάθημα (xlsx + ics) -> Αρχεία -> Ρυθμίσεις

$(mark unknown) ${B}6. Ασφάλεια δεδομένων${N}
     - Αντίγραφο του $DIR/.env σε ασφαλές μέρος (π.χ. password manager).
     - Backups και εκτός server:  rsync -a $(id -un)@$ip:$DIR/backups/ ./study-backups/

${D}Έλεγχος ξανά:      cd $DIR && ./install.sh --check
Update:            cd $DIR && git pull && ./install.sh
Αλλαγή ρυθμίσεων:  cd $DIR && STUDY_RECONFIGURE=1 ./install.sh
Logs:              docker compose logs -f app${N}
EOF
}

write_next_steps_file() {
  ( B=; D=; R=; G=; Y=; C=; N=; print_next_steps ) >"$DIR/next-steps.txt"
  chmod 600 "$DIR/next-steps.txt"
}

load_saved_settings() {
  APP_DOMAIN=$(env_get APP_BASE_URL); APP_DOMAIN=${APP_DOMAIN#*://}
  NTFY_DOMAIN=$(env_get NTFY_PUBLIC_URL); NTFY_DOMAIN=${NTFY_DOMAIN#*://}
  TOPIC=$(env_get NTFY_TOPIC); PROXY_NETWORK=$(env_get PROXY_NETWORK); DATA_DIR=$(env_get STUDY_DATA_DIR)
  MAX_MB=$(env_get MAX_UPLOAD_MB); MAX_MB=${MAX_MB:-50}
  if [ "$(env_get TRUSTED_PROXY_HOPS)" = 2 ]; then CF_MODE=proxied; else CF_MODE=dns; fi
}

# ---------------------------------------------------------------- ./install.sh --check

if [ "${1-}" = "--check" ]; then
  [ -n "$(env_get SECRET_KEY)" ] || die "Δεν βρέθηκε εγκατάσταση (.env). Τρέξε πρώτα ./install.sh"
  load_saved_settings
  step "Έλεγχος εγκατάστασης"
  run_checks
  print_next_steps
  write_next_steps_file
  exit 0
fi

# ================================================================ ΕΓΚΑΤΑΣΤΑΣΗ

step "Έλεγχος προαπαιτούμενων"
[ -f docker-compose.yml ] && [ -f Dockerfile ] || die "Τρέξε το script μέσα από τον φάκελο της εφαρμογής."
[ "$(id -u)" -ne 0 ] || warn "Τρέχεις ως root. Προτείνεται κανονικός χρήστης που ανήκει στο group docker."
command -v docker >/dev/null || die "Δεν βρέθηκε το docker. Εγκατάστασέ το: https://docs.docker.com/engine/install/"
docker compose version >/dev/null 2>&1 || die "Χρειάζεται docker compose v2 (το plugin 'docker compose')."
docker info >/dev/null 2>&1 || die "Ο χρήστης $(id -un) δεν έχει πρόσβαση στο Docker. Τρέξε: sudo usermod -aG docker $(id -un)  , κάνε αποσύνδεση/σύνδεση και ξανατρέξε ./install.sh"
command -v curl >/dev/null || command -v wget >/dev/null || warn "Δεν υπάρχει curl ή wget: δεν θα μπορώ να ελέγξω το HTTPS (sudo apt install curl)."
ok "Docker $(docker version -f '{{.Server.Version}}'), $(docker compose version --short 2>/dev/null || echo compose v2)"
ok "Φάκελος εγκατάστασης: $DIR"

REUSE=no
if [ -f "$ENV_FILE" ] && [ -n "$(env_get SECRET_KEY)" ] && [ "${STUDY_RECONFIGURE-}" != 1 ]; then
  step "Υπάρχουσα εγκατάσταση"
  info "Βρέθηκαν ρυθμίσεις (.env) από προηγούμενη εγκατάσταση."
  info "ναι = update/επανεκκίνηση με τις ίδιες ρυθμίσεις · όχι = ξαναρωτάω τα πάντα."
  if confirm "Να κρατηθούν οι υπάρχουσες ρυθμίσεις;" y; then REUSE=yes; fi
fi

detect_ips

# Όλες οι ερωτήσεις μαζί. Σε διόρθωση, οι προηγούμενες απαντήσεις γίνονται οι προτεινόμενες.
collect_settings() {
  local def n img name net

  step "Ερωτήσεις 1/4: Διευθύνσεις (domains)"
  [ -z "$PUBLIC_IP" ] || info "Η δημόσια IP αυτού του server είναι ${B}$PUBLIC_IP${N}."
  field "Διεύθυνση του dashboard" \
    "Ένα subdomain του domain σου που έχεις στο Cloudflare. Από εκεί θα ανοίγεις την εφαρμογή." \
    "Δεν χρειάζεται να υπάρχει ήδη, θα το φτιάξεις σε λίγο. Παράδειγμα: study.example.com"
  ask STUDY_APP_DOMAIN "Γράψε το domain του dashboard" "${PREV_STUDY_APP_DOMAIN-}" v_domain
  APP_DOMAIN=$STUDY_APP_DOMAIN

  field "Διεύθυνση των ειδοποιήσεων (ntfy)" \
    "Δεύτερο, διαφορετικό subdomain. Σε αυτό θα συνδέεται η εφαρμογή ntfy του κινητού." \
    "Παράδειγμα: ntfy.example.com"
  def=${PREV_STUDY_NTFY_DOMAIN-}
  if [ -z "$def" ] && [[ $APP_DOMAIN == *.*.* ]] && [ "${APP_DOMAIN%%.*}" != ntfy ]; then def="ntfy.${APP_DOMAIN#*.}"; fi
  ask STUDY_NTFY_DOMAIN "Γράψε το domain του ntfy" "$def" v_ntfy_domain
  NTFY_DOMAIN=$STUDY_NTFY_DOMAIN

  field "Cloudflare: τι Proxy status θα έχουν τα DNS records;" \
    "${B}1${N} = DNS only (γκρι σύννεφο). Πιο απλό, προτείνεται." \
    "${B}2${N} = Proxied (πορτοκαλί σύννεφο). Κρύβει την IP σου, θέλει επιπλέον ένα API token του Cloudflare."
  ask STUDY_CLOUDFLARE "Γράψε 1 ή 2" "${PREV_STUDY_CLOUDFLARE:-1}" v_cloudflare
  CF_MODE=$STUDY_CLOUDFLARE

  step "Ερωτήσεις 2/4: Λογαριασμός dashboard"
  field "Όνομα χρήστη" "Με αυτό θα κάνεις login στο dashboard. Το διαλέγεις εσύ τώρα."
  ask STUDY_ADMIN_USER "Γράψε όνομα χρήστη" "${PREV_STUDY_ADMIN_USER:-admin}" v_user
  field "Κωδικός dashboard" \
    "Νέος κωδικός για το login, τουλάχιστον 10 χαρακτήρες. Θα τον γράψεις δύο φορές." \
    "${Y}Καθώς πληκτρολογείς δεν εμφανίζεται τίποτα στην οθόνη. Είναι φυσιολογικό.${N}"
  ask_secret STUDY_ADMIN_PASSWORD "Γράψε κωδικό dashboard" 10 no

  step "Ερωτήσεις 3/4: Ειδοποιήσεις"
  field "Topic" "Το όνομα του «καναλιού» στο οποίο θα γραφτεί το κινητό. Αν δεν σε νοιάζει, πάτα Enter."
  ask STUDY_NTFY_TOPIC "Γράψε όνομα topic" "${PREV_STUDY_NTFY_TOPIC:-study}" v_topic
  TOPIC=$STUDY_NTFY_TOPIC
  field "Κωδικός για το κινητό" \
    "Ξεχωριστός κωδικός για την εφαρμογή ntfy του κινητού (χρήστης: phone), τουλάχιστον 8 χαρακτήρες." \
    "Με σκέτο Enter φτιάχνεται τυχαίος και σου τον δείχνω όταν συνδέσεις το κινητό." \
    "${Y}Αν τον γράψεις εσύ, δεν εμφανίζεται στην οθόνη.${N}"
  ask_secret STUDY_PHONE_PASSWORD "Γράψε κωδικό κινητού" 8 yes

  step "Ερωτήσεις 4/4: Server"
  field "Ζώνη ώρας" "Για να έρχονται οι ειδοποιήσεις στη σωστή ώρα. Μορφή Ήπειρος/Πόλη."
  ask STUDY_TZ "Γράψε ζώνη ώρας" "${PREV_STUDY_TZ:-Europe/Athens}" v_tz
  field "Μέγιστο μέγεθος αρχείου" "Πόσο μεγάλο PDF/εικόνα θα μπορείς να ανεβάσεις, σε MB."
  ask STUDY_MAX_UPLOAD_MB "Γράψε αριθμό MB" "${PREV_STUDY_MAX_UPLOAD_MB:-50}" v_mb
  MAX_MB=$STUDY_MAX_UPLOAD_MB
  field "Φάκελος δεδομένων" \
    "Εκεί αποθηκεύονται η βάση, τα PDF και οι χρήστες του ntfy. Πάτα Enter για τον προτεινόμενο," \
    "ή γράψε άλλη διαδρομή (π.χ. /srv/study) ή τη λέξη volumes για docker volumes."
  ask STUDY_DATA "Γράψε φάκελο" "${PREV_STUDY_DATA:-$DIR/data}" v_datadir
  if [ "$STUDY_DATA" = volumes ]; then DATA_DIR=""; else
    DATA_DIR=$STUDY_DATA; [[ $DATA_DIR = /* ]] || DATA_DIR="$DIR/$DATA_DIR"; DATA_DIR=${DATA_DIR%/}
  fi

  field "Container του Nginx Proxy Manager" \
    "Για να βλέπει ο NPM την εφαρμογή, πρέπει να τρέχει σε Docker ${B}σε αυτόν τον server${N}." \
    "Containers που τρέχουν τώρα:"
  CHOICES=(); n=0; def=""
  while IFS=$'\t' read -r name img; do
    case $name in ''|study-app|study-ntfy) continue ;; esac
    CHOICES+=("$name"); n=$((n + 1))
    line "  ${B}$n${N}) $name  ${D}($img)${N}"
    if [ -z "$def" ] && [[ ${img,,} == *nginx-proxy-manager* ]]; then def=$name; fi
  done < <(docker ps --format '{{.Names}}\t{{.Image}}')
  [ "$n" -gt 0 ] || line "  ${D}(κανένα)${N}"
  line "Γράψε τον αριθμό ή το όνομα του NPM. Γράψε ${B}-${N} αν δεν είναι στη λίστα και θα το συνδέσεις αργότερα."
  ask STUDY_NPM_CONTAINER "Γράψε αριθμό, όνομα ή -" "${PREV_STUDY_NPM_CONTAINER:-${def:--}}" v_npm
  NPM=$STUDY_NPM_CONTAINER

  CHOICES=(); n=0
  if [ "$NPM" != - ]; then
    for net in $(networks_of "$NPM"); do
      case $net in bridge|host|none) continue ;; esac
      CHOICES+=("$net")
    done
  fi
  field "Κοινό docker network με τον NPM" "Το εσωτερικό δίκτυο μέσα από το οποίο ο NPM θα μιλά με την εφαρμογή."
  if [ "${#CHOICES[@]}" -gt 0 ]; then
    line "Δίκτυα στα οποία είναι ήδη ο NPM:"
    for net in "${CHOICES[@]}"; do n=$((n + 1)); line "  ${B}$n${N}) $net"; done
    line "Πάτα Enter για το προτεινόμενο."
    def=${CHOICES[0]}
    if [[ " ${CHOICES[*]} " == *" ${PREV_STUDY_PROXY_NETWORK-} "* ]]; then def=${PREV_STUDY_PROXY_NETWORK:-$def}; fi
  else
    line "Πάτα Enter για το προτεινόμενο. Αν δεν υπάρχει, θα το δημιουργήσω."
    def=${PREV_STUDY_PROXY_NETWORK:-npm_proxy}
  fi
  ask STUDY_PROXY_NETWORK "Γράψε αριθμό ή όνομα δικτύου" "$def" v_network
  PROXY_NETWORK=$STUDY_PROXY_NETWORK
}

summary() {
  step "Σύνοψη των απαντήσεων"
  line "Dashboard:            https://$APP_DOMAIN"
  line "Ειδοποιήσεις (ntfy):  https://$NTFY_DOMAIN"
  line "Cloudflare:           $(cf_cloud)"
  line "Χρήστης dashboard:    $STUDY_ADMIN_USER   ${D}(ο κωδικός δεν εμφανίζεται)${N}"
  line "Topic:                $TOPIC"
  line "Κωδικός κινητού:      $([ "${STUDY_PHONE_PASSWORD_GENERATED-}" = yes ] && echo "τυχαίος" || echo "αυτός που έδωσες")"
  line "Ζώνη ώρας:            $STUDY_TZ"
  line "Μέγιστο αρχείο:       $MAX_MB MB"
  line "Δεδομένα:             ${DATA_DIR:-docker volumes}"
  line "Container NPM:        $NPM"
  line "Docker network:       $PROXY_NETWORK"
}

# Σε επανα-ρύθμιση, οι αποθηκευμένες τιμές προτείνονται ως απαντήσεις.
prefill_from_env() {
  [ -n "$(env_get SECRET_KEY)" ] || return 0
  load_saved_settings
  PREV_STUDY_APP_DOMAIN=$APP_DOMAIN; PREV_STUDY_NTFY_DOMAIN=$NTFY_DOMAIN
  if [ "$CF_MODE" = proxied ]; then PREV_STUDY_CLOUDFLARE=2; else PREV_STUDY_CLOUDFLARE=1; fi
  PREV_STUDY_ADMIN_USER=$(env_get ADMIN_USER); PREV_STUDY_NTFY_TOPIC=$TOPIC
  PREV_STUDY_TZ=$(env_get DEFAULT_TZ); PREV_STUDY_MAX_UPLOAD_MB=$MAX_MB
  PREV_STUDY_DATA=${DATA_DIR:-volumes}; PREV_STUDY_PROXY_NETWORK=$PROXY_NETWORK
}

# Κρατά τις απαντήσεις ως προτεινόμενες και αδειάζει όσες δεν ήρθαν από το περιβάλλον.
reset_answers() {
  local v
  for v in $SETTINGS; do
    printf -v "PREV_$v" '%s' "${!v}"
    [[ $PRESET == *" $v "* ]] || printf -v "$v" '%s' ""
  done
  if [ "$PREV_STUDY_CLOUDFLARE" = proxied ]; then PREV_STUDY_CLOUDFLARE=2; else PREV_STUDY_CLOUDFLARE=1; fi
  # Ο τυχαίος κωδικός κινητού ξαναφτιάχνεται με Enter.
  [ "${STUDY_PHONE_PASSWORD_GENERATED-}" != yes ] || PREV_STUDY_PHONE_PASSWORD=""
}

if [ "$REUSE" = yes ]; then
  load_saved_settings
else
  step "Πριν ξεκινήσουμε"
  info "Θα χρειαστείς (καλό είναι να τα έχεις ανοιχτά):"
  line "• Τον λογαριασμό ${B}Cloudflare${N} με το domain σου  (https://dash.cloudflare.com)"
  line "• Το web UI του ${B}Nginx Proxy Manager${N}  (http://${LOCAL_IP:-<IP-του-server>}:81) και τον κωδικό του"
  line "• Πρόσβαση στο ${B}router${N} (αν ο server είναι στο σπίτι) ή στο firewall του παρόχου"
  line "• Το ${B}κινητό${N} σου"
  info ""
  info "Σειρά: ερωτήσεις -> Cloudflare -> θύρες -> εγκατάσταση -> NPM -> κινητό -> backup."
  info "Όπου υπάρχει ${C}➜${N} περιμένω κάτι από εσένα και γράφει τι. Διακοπή: Ctrl+C."
  pause "Πάτα [Enter] για να ξεκινήσουμε:"

  [ "$INTERACTIVE" = no ] || prefill_from_env
  while :; do
    collect_settings
    summary
    if confirm "Είναι όλα σωστά;" y; then break; fi
    info "Θα ξαναρωτήσω. Με Enter κρατάς την προηγούμενη απάντηση."
    reset_answers
  done

  # ------------------------------------------------------------ χειροκίνητα: Cloudflare + θύρες
  step "Βήμα 1/6: DNS στο Cloudflare"
  guide "DNS στο Cloudflare" check_dns_both dns_instructions || true

  step "Βήμα 2/6: Θύρες 80 και 443"
  ports_instructions
  pause "Άνοιξε τις θύρες και μετά πάτα [Enter] (θα επιβεβαιωθούν στο βήμα του NPM):"
fi

if docker network inspect "$PROXY_NETWORK" >/dev/null 2>&1; then
  ok "Το δίκτυο $PROXY_NETWORK υπάρχει."
else
  docker network create "$PROXY_NETWORK" >/dev/null
  ok "Δημιουργήθηκε το δίκτυο $PROXY_NETWORK."
fi
if [ "$REUSE" = no ] && [ "$NPM" = - ]; then
  todo "Σύνδεσε τον NPM στο ίδιο δίκτυο (πριν φτιάξεις τα Proxy Hosts)"
  line "Βρες το όνομα του container του NPM με ${B}docker ps${N} και τρέξε:"
  line "${B}docker network connect $PROXY_NETWORK <όνομα-container-NPM>${N}"
  pause "Πάτα [Enter] για συνέχεια:"
elif [ "$REUSE" = no ]; then
  if [[ " $(networks_of "$NPM") " == *" $PROXY_NETWORK "* ]]; then
    ok "Το NPM είναι ήδη στο $PROXY_NETWORK."
  else
    docker network connect "$PROXY_NETWORK" "$NPM"
    ok "Το NPM ($NPM) συνδέθηκε στο $PROXY_NETWORK."
    todo "Κάνε τη σύνδεση μόνιμη (αλλιώς χάνεται αν ξαναδημιουργηθεί το container του NPM)"
    line "Άνοιξε το docker-compose.yml ${B}του NPM${N}, πρόσθεσε τα παρακάτω και τρέξε εκεί ${B}docker compose up -d${N}."
    line "${D}Αν υπάρχουν ήδη ενότητες «networks:», πρόσθεσε μόνο τις νέες γραμμές μέσα τους.${N}"
    printf '\n%s\n' "services:" "  app:                  # το όνομα του service του NPM" "    networks:" \
      "      - default" "      - $PROXY_NETWORK" "networks:" "  $PROXY_NETWORK:" "    external: true"
    echo
    pause "Μπορείς να το κάνεις τώρα ή αργότερα. Πάτα [Enter] για συνέχεια:"
  fi
fi

# ---------------------------------------------------------------- .env, φάκελοι, build

step "Βήμα 3/6: Εγκατάσταση εφαρμογής"
info "Εδώ δεν χρειάζεται να κάνεις κάτι, απλώς περίμενε."
if [ "$REUSE" = no ]; then
  old_secret=$(env_get SECRET_KEY); old_token=$(env_get NTFY_TOKEN)
  [ -f "$ENV_FILE" ] && cp -p "$ENV_FILE" "$ENV_FILE.bak.$(date +%s)" && info "Κράτησα αντίγραφο του παλιού .env."
  umask 077
  cat >"$ENV_FILE" <<EOF
# Δημιουργήθηκε από το install.sh στις $(date '+%Y-%m-%d %H:%M'). Μην το ανεβάσεις ποτέ σε git.
APP_BASE_URL='https://$APP_DOMAIN'
NTFY_PUBLIC_URL='https://$NTFY_DOMAIN'
SECRET_KEY='${old_secret:-$(randstr 64)}'
ADMIN_USER='$STUDY_ADMIN_USER'
ADMIN_PASSWORD_HASH=''
NTFY_TOKEN='$old_token'
NTFY_TOPIC='$TOPIC'
MAX_UPLOAD_MB='$STUDY_MAX_UPLOAD_MB'
DEFAULT_TZ='$STUDY_TZ'
COOKIE_SECURE='true'
TRUSTED_PROXY_HOPS='$([ "$CF_MODE" = proxied ] && echo 2 || echo 1)'
PROXY_NETWORK='$PROXY_NETWORK'
STUDY_DATA_DIR='$DATA_DIR'
EOF
  chmod 600 "$ENV_FILE"
  umask 022
  ok ".env γράφτηκε (δικαιώματα 600)."

  if [ -n "$DATA_DIR" ]; then
    mkdir -p "$DATA_DIR/app" "$DATA_DIR/ntfy" "$DIR/backups"
    cat >"$OVERRIDE" <<EOF
# Δημιουργήθηκε από το install.sh: τα δεδομένα αποθηκεύονται στο $DATA_DIR
services:
  app:
    volumes:
      - $DATA_DIR/app:/data
  ntfy:
    volumes:
      - $DATA_DIR/ntfy:/var/lib/ntfy
  ntfy-init:
    volumes:
      - $DATA_DIR/ntfy:/var/lib/ntfy
EOF
    # Τα containers τρέχουν ως uid 10001 (app) και 10002 (ntfy): δικαιώματα μέσω docker, χωρίς sudo.
    docker run --rm -v "$DATA_DIR/app:/a" -v "$DATA_DIR/ntfy:/n" busybox:1.36 \
      sh -c 'chown -R 10001:10001 /a && chown -R 10002:10002 /n' >/dev/null
    ok "Δεδομένα στο $DATA_DIR"
  else
    rm -f "$OVERRIDE"
    mkdir -p "$DIR/backups"
    ok "Δεδομένα σε docker volumes (study_study-data, study_ntfy-data)."
  fi
fi

info "Build (την πρώτη φορά παίρνει 1-3 λεπτά)..."
docker compose build --pull -q app
ok "Το image χτίστηκε."

if [ "$REUSE" = no ]; then
  HASH=$(printf '%s' "$STUDY_ADMIN_PASSWORD" | docker compose run --rm -T --no-deps --entrypoint python app -c \
    'import sys, bcrypt; print(bcrypt.hashpw(sys.stdin.buffer.read()[:72], bcrypt.gensalt(12)).decode())' 2>/dev/null)
  [[ $HASH == \$2* ]] || die "Δεν δημιουργήθηκε hash κωδικού."
  env_set ADMIN_PASSWORD_HASH "$HASH"
  ok "Ο κωδικός αποθηκεύτηκε ως bcrypt hash."
fi

if ! up_out=$(docker compose up -d --remove-orphans 2>&1); then printf '%s
' "$up_out" >&2; die "Το docker compose up απέτυχε."; fi
wait_healthy study-ntfy

if ! ntfy_user_exists studyapp; then
  docker compose exec -T -e NTFY_PASSWORD="$(randstr 32)" ntfy ntfy user add studyapp >/dev/null 2>&1
  ok "ntfy: χρήστης studyapp (αποστολή)."
fi
docker compose exec -T ntfy ntfy access studyapp "$TOPIC" write-only >/dev/null 2>&1
if [ -z "$(env_get NTFY_TOKEN)" ]; then
  TOKEN=$(docker compose exec -T ntfy ntfy token add studyapp 2>&1 | grep -oE -m1 'tk_[A-Za-z0-9]+' || true)
  [ -n "$TOKEN" ] || die "Δεν δημιουργήθηκε token στο ntfy."
  env_set NTFY_TOKEN "$TOKEN"
  ok "ntfy: token αποστολής γράφτηκε στο .env."
fi
if [ "$REUSE" = no ]; then
  if ntfy_user_exists phone; then
    docker compose exec -T -e NTFY_PASSWORD="$STUDY_PHONE_PASSWORD" ntfy ntfy user change-pass phone >/dev/null 2>&1
    ok "ntfy: ενημερώθηκε ο κωδικός του χρήστη phone."
  else
    docker compose exec -T -e NTFY_PASSWORD="$STUDY_PHONE_PASSWORD" ntfy ntfy user add phone >/dev/null 2>&1
    ok "ntfy: χρήστης phone (κινητό, μόνο ανάγνωση)."
  fi
fi
docker compose exec -T ntfy ntfy access phone "$TOPIC" read-only >/dev/null 2>&1
if ! up_out=$(docker compose up -d app 2>&1); then printf '%s
' "$up_out" >&2; die "Η επανεκκίνηση της εφαρμογής απέτυχε."; fi   # ξαναφορτώνει το .env με το token
wait_healthy study-app
ok "Η εφαρμογή και το ntfy τρέχουν."

# ---------------------------------------------------------------- 10-11. NPM proxy hosts (με έλεγχο HTTPS)

if [ "$REUSE" = no ]; then
  step "Βήμα 4/6: Nginx Proxy Manager (δύο Proxy Hosts)"
  info "Πρώτα της εφαρμογής."
  guide "HTTPS: $APP_DOMAIN" check_https_app npm_instructions_app || true

  info ""
  info "Τώρα του ntfy."
  guide "HTTPS: $NTFY_DOMAIN" check_https_ntfy npm_instructions_ntfy || true

  # ------------------------------------------------------------ 12. κινητό
  step "Βήμα 5/6: Κινητό"
  phone_instructions
  if check_https_ntfy; then
    pause "Κάνε τα βήματα 1-4 στο κινητό και μετά πάτα [Enter] να σου στείλω δοκιμαστική ειδοποίηση:"
    while :; do
      if send_test_notification "future-me-will-study · Η σύνδεση του κινητού δουλεύει"; then
        ok "Στάλθηκε δοκιμαστική ειδοποίηση στο topic '$TOPIC'."
      else
        warn "Η αποστολή απέτυχε. Logs: docker compose logs --tail 50 app ntfy"
      fi
      if [ "$INTERACTIVE" = no ] || confirm "Ήρθε η ειδοποίηση στο κινητό;" y; then break; fi
      phone_troubleshooting
      confirm "Να τη στείλω ξανά;" y || break
    done
  else
    warn "Το https://$NTFY_DOMAIN δεν απαντά ακόμα, οπότε το κινητό δεν μπορεί να συνδεθεί."
    info "Όταν ολοκληρώσεις το Proxy Host του ntfy, κάνε τη σύνδεση στο κινητό και στείλε δοκιμαστική"
    info "από το dashboard: Ρυθμίσεις -> Δοκιμαστική ειδοποίηση."
  fi
else
  send_test_notification "future-me-will-study · Η ενημέρωση ολοκληρώθηκε" && ok "Στάλθηκε δοκιμαστική ειδοποίηση." || \
    warn "Η δοκιμαστική ειδοποίηση απέτυχε. Logs: docker compose logs app ntfy"
fi

# ---------------------------------------------------------------- 13. backup

step "Βήμα 6/6: Backup"
info "Αντίγραφο της βάσης και των αρχείων στον φάκελο $DIR/backups."
chmod +x "$DIR/backup.sh"
if command -v crontab >/dev/null; then
  current_cron=$(crontab -l 2>/dev/null || true)
  if [[ $current_cron == *"# study-backup"* ]]; then
    ok "Το καθημερινό backup είναι ήδη προγραμματισμένο."
  elif confirm "Να προγραμματιστεί καθημερινό backup στις 03:30;" y; then
    cron_line="30 3 * * * $DIR/backup.sh >>$DIR/backups/backup.log 2>&1 # study-backup"
    { [ -n "$current_cron" ] && printf '%s\n' "$current_cron"; echo "$cron_line"; } | crontab -
    ok "Backup κάθε μέρα στις 03:30 στο $DIR/backups (κρατούνται τα 14 τελευταία)."
  fi
else
  warn "Δεν υπάρχει crontab. Τρέχε χειροκίνητα: $DIR/backup.sh"
fi

# ---------------------------------------------------------------- τέλος

run_checks
write_next_steps_file
if [ "$ALL_OK" = yes ]; then
  step "Έτοιμο!"
  ok "DNS, HTTPS και ειδοποιήσεις λειτουργούν."
  todo "Πρώτη ρύθμιση στο dashboard"
  line "1. Άνοιξε ${B}https://$APP_DOMAIN${N} και συνδέσου ως ${B}$(env_get ADMIN_USER)${N}."
  line "2. ${B}Μαθήματα -> Νέο μάθημα${N}: όνομα, ημερομηνία εξέτασης, xlsx πλάνου (και .ics αν υπάρχει)."
  line "3. ${B}Αρχεία${N}: ανέβασε τα PDF ανά κατηγορία (π.χ. L01-L13 στις «Ημερήσιες σημειώσεις»)."
  line "4. ${B}Ρυθμίσεις${N}: ώρες ειδοποιήσεων -> Αποθήκευση."
  line ""
  todo "Ασφάλεια δεδομένων"
  line "• Κράτα αντίγραφο του $DIR/.env σε ασφαλές μέρος (π.χ. password manager)."
  line "• Κράτα τα backups και εκτός server. Τρέξε στον ${B}δικό σου υπολογιστή${N}:"
  line "    ${B}scp -r $(id -un)@${PUBLIC_IP:-<server>}:$DIR/backups ./study-backups${N}"
  line "${D}Έλεγχος οποτεδήποτε: ./install.sh --check · Update: git pull && ./install.sh${N}"
else
  step "Η εγκατάσταση ολοκληρώθηκε, αλλά μένουν εκκρεμότητες"
  print_next_steps
  info "Όταν τις κάνεις:  ${B}cd $DIR && ./install.sh --check${N}"
fi
printf '\n%sΗ κατάσταση αποθηκεύτηκε στο %s/next-steps.txt%s\n' "$D" "$DIR" "$N"
