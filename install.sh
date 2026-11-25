#!/usr/bin/env bash
# Εγκατάσταση του future-me-will-study σε Linux server, κάτω από τον τρέχοντα χρήστη.
#
#   git clone <repo> ~/study && cd ~/study && ./install.sh
#   ./install.sh --check      (μόνο έλεγχος DNS, θυρών, HTTPS και λίστα εκκρεμοτήτων)
#
# Οδηγός βήμα-βήμα: κάθε χειροκίνητη ενέργεια (DNS, θύρες, Nginx Proxy Manager, κινητό) εμφανίζεται
# τη στιγμή που χρειάζεται, και το script ελέγχει ότι έγινε πριν προχωρήσει.
# Μπορεί να ξανατρέξει με ασφάλεια (π.χ. μετά από update). Για αυτοματοποίηση, κάθε ερώτηση
# παίρνει τιμή από env var (STUDY_APP_DOMAIN, STUDY_ADMIN_PASSWORD, ...) και STUDY_ASSUME_YES=1.
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

# ---------------------------------------------------------------- βοηθητικά

ask() { # ask VAR "ερώτηση" [default]
  local var=$1 q=$2 def=${3-} ans=""
  [ -n "${!var-}" ] && return 0
  while :; do
    ans=""
    if ! read -r -p "    $q${def:+ [$def]}: " ans 2>/dev/null <&3; then
      [ -n "$def" ] || die "Χρειάζεται τερματικό για την ερώτηση «$q» (ή όρισε τη μεταβλητή $var)."
      echo
    fi
    ans=${ans:-$def}
    [ -n "$ans" ] && break
  done
  printf -v "$var" '%s' "$ans"
}

ask_secret() { # ask_secret VAR "ερώτηση" minlen allow_random(yes/no)
  local var=$1 q=$2 min=$3 rnd=$4 a="" b=""
  if [ "${!var-}" = random ] && [ "$rnd" = yes ]; then
    printf -v "$var" '%s' "$(randstr 20)"; printf -v "${var}_GENERATED" '%s' yes; return 0
  fi
  [ -n "${!var-}" ] && return 0
  [ "$INTERACTIVE" = yes ] || die "Χρειάζεται τερματικό για την ερώτηση «$q» (ή όρισε τη μεταβλητή $var)."
  while :; do
    a=""; b=""
    if [ "$rnd" = yes ]; then read -r -s -p "    $q (Enter = τυχαίος): " a <&3 || true
    else read -r -s -p "    $q: " a <&3 || true; fi
    echo
    if [ -z "$a" ] && [ "$rnd" = yes ]; then a=$(randstr 20); printf -v "${var}_GENERATED" '%s' yes; break; fi
    if [ ${#a} -lt "$min" ]; then warn "Τουλάχιστον $min χαρακτήρες."; continue; fi
    read -r -s -p "    Ξανά για επιβεβαίωση: " b <&3 || true; echo
    [ "$a" = "$b" ] && break
    warn "Δεν ταιριάζουν, δοκίμασε ξανά."
  done
  printf -v "$var" '%s' "$a"
}

confirm() { # confirm "ερώτηση" y|n
  local q=$1 def=${2:-y} ans hint
  if [ "$INTERACTIVE" = no ]; then [ "$def" = y ]; return; fi
  [ "$def" = y ] && hint="Ν/ο" || hint="ν/Ο"
  read -r -p "    $q [$hint]: " ans <&3 || true
  ans=${ans:-$def}
  case $ans in y*|Y*|ν*|Ν*) return 0 ;; *) return 1 ;; esac
}

pause() { # pause "μήνυμα"
  [ "$INTERACTIVE" = yes ] || return 0
  local _
  read -r -p "    $C➜$N $1" _ <&3 || true
}

todo() { printf '\n    %s➜ Κάνε τώρα:%s %s%s%s\n' "$C" "$N" "$B" "$*" "$N"; }
line() { printf '      %s\n' "$*"; }

# guide "περιγραφή" check_fn instructions_fn
# Αν ο έλεγχος περνά ήδη, δεν δείχνει τίποτα. Αλλιώς δείχνει οδηγίες και περιμένει (Enter = ξανά, s = αργότερα).
guide() {
  local what=$1 check=$2 instr=$3 ans first=yes prompt
  CHECK_DETAIL=""
  if "$check"; then ok "$what ${D}($CHECK_DETAIL)${N}"; return 0; fi
  "$instr"
  if [ "$INTERACTIVE" = no ]; then warn "$what: εκκρεμεί ($CHECK_DETAIL)."; return 1; fi
  while :; do
    if [ "$first" = yes ]; then
      prompt="Όταν το κάνεις, πάτα [Enter] για έλεγχο · [s] για να το αφήσεις για αργότερα: "; first=no
    else
      prompt="${Y}Όχι ακόμα${N} ($CHECK_DETAIL). [Enter] ξανά · [s] αργότερα: "
    fi
    read -r -p "    $C➜$N $prompt" ans <&3 || ans=s
    case $ans in s|S|σ|Σ) warn "$what: το αφήνεις για αργότερα (έλεγχος: ./install.sh --check)."; return 1 ;; esac
    if "$check"; then ok "$what ${D}($CHECK_DETAIL)${N}"; return 0; fi
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
  elif [ -n "$PUBLIC_IP" ] && [ "$1" != "$PUBLIC_IP" ]; then echo warn
  else echo ok; fi
}

port_listening() { local out; out=$( (ss -ltnH 2>/dev/null || netstat -ltn 2>/dev/null) || true); grep -qE "[:.]$1[[:space:]]" <<<"$out"; }

container_health() { docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$1" 2>/dev/null || echo "δεν υπάρχει"; }

# --- έλεγχοι για τον οδηγό (θέτουν CHECK_DETAIL)
check_dns_for() { # check_dns_for host
  local host=${1%%:*} r
  r=$(resolve "$host")
  if [ -z "$r" ]; then CHECK_DETAIL="το $host δεν επιλύεται ακόμα"; return 1; fi
  if [ -n "$PUBLIC_IP" ] && [ "$r" != "$PUBLIC_IP" ]; then
    CHECK_DETAIL="το $host δείχνει στο $r αντί για $PUBLIC_IP"; return 1
  fi
  CHECK_DETAIL="$host -> $r"; return 0
}
check_dns_app()  { check_dns_for "$APP_DOMAIN"; }
check_dns_ntfy() { check_dns_for "$NTFY_DOMAIN"; }
check_https_app() {
  local body; body=$(http_get "https://$APP_DOMAIN/healthz")
  if [[ $body == *'"ok":true'* ]]; then CHECK_DETAIL="https://$APP_DOMAIN απαντά"; return 0; fi
  CHECK_DETAIL="το https://$APP_DOMAIN/healthz δεν απαντά ακόμα"; return 1
}
check_https_ntfy() {
  local body; body=$(http_get "https://$NTFY_DOMAIN/v1/health")
  if [[ $body == *healthy*true* ]]; then CHECK_DETAIL="https://$NTFY_DOMAIN απαντά"; return 0; fi
  CHECK_DETAIL="το https://$NTFY_DOMAIN/v1/health δεν απαντά ακόμα"; return 1
}

# --- οδηγίες τη στιγμή που χρειάζονται
dns_instructions_for() { # dns_instructions_for host
  local host=${1%%:*}
  todo "DNS record για το $host"
  line "Στον πάροχο του domain σου (Cloudflare, Papaki, Namecheap, ...) -> DNS -> Add record:"
  line ""
  line "  Τύπος:  ${B}A${N}"
  line "  Όνομα:  ${B}${host%%.*}${N}      ${D}(σε κάποιους παρόχους γράφεις ολόκληρο το $host)${N}"
  if [ -n "$PUBLIC_IP" ]; then line "  Τιμή:   ${B}$PUBLIC_IP${N}"
  else line "  Τιμή:   ${B}η δημόσια IP του server${N} ${D}(δεν μπόρεσα να τη βρω· δες τη στον πάροχό σου)${N}"; fi
  line "  TTL:    Auto ή 300"
  line ""
  line "${D}Cloudflare: διάλεξε «DNS only» (γκρι σύννεφο). Αν ο server έχει IPv6, πρόσθεσε και AAAA record.${N}"
  line "${D}Η αλλαγή φαίνεται συνήθως μέσα σε 1-5 λεπτά.${N}"
}
dns_instructions_app()  { dns_instructions_for "$APP_DOMAIN"; }
dns_instructions_ntfy() { dns_instructions_for "$NTFY_DOMAIN"; }

ports_instructions() {
  todo "Άνοιξε τις θύρες 80 και 443 προς τον server"
  if [ "$BEHIND_NAT" = yes ]; then
    line "Ο server έχει ιδιωτική IP (${B}$LOCAL_IP${N}), άρα είναι πίσω από router. Στη σελίδα του router:"
    line ""
    line "  Port forwarding:  TCP ${B}80${N}  ->  ${B}$LOCAL_IP:80${N}"
    line "  Port forwarding:  TCP ${B}443${N} ->  ${B}$LOCAL_IP:443${N}"
    line ""
    line "${D}Αν η IP της σύνδεσής σου αλλάζει, ενεργοποίησε Dynamic DNS (στο router ή π.χ. Cloudflare DDNS).${N}"
  else
    line "Στο firewall του παρόχου (π.χ. Hetzner Firewall, Oracle Security List, AWS Security Group):"
    line "  επίτρεψε εισερχόμενα ${B}TCP 80${N} και ${B}TCP 443${N} από παντού."
    line "Αν χρησιμοποιείς ufw σε αυτόν τον server:   ${B}sudo ufw allow 80,443/tcp${N}"
  fi
  line "${D}Η θύρα 81 (διαχείριση του NPM) να ΜΗΝ είναι ανοιχτή προς το internet.${N}"
  if port_listening 80 && port_listening 443; then
    line "${G}✓${N} Ο Nginx Proxy Manager ακούει ήδη στις 80/443 σε αυτόν τον server."
  else
    line "${R}✗${N} Τίποτα δεν ακούει στις 80/443 εδώ. Βεβαιώσου ότι τρέχει ο Nginx Proxy Manager."
  fi
}

npm_instructions_app() {
  todo "Proxy Host για την εφαρμογή στο Nginx Proxy Manager"
  line "Άνοιξε το NPM (συνήθως http://<IP-server>:81) -> Hosts -> Proxy Hosts -> ${B}Add Proxy Host${N}"
  line ""
  line "Καρτέλα ${B}Details${N}"
  line "  Domain Names:           ${B}${APP_DOMAIN%%:*}${N}"
  line "  Scheme:                 ${B}http${N}"
  line "  Forward Hostname / IP:  ${B}study-app${N}"
  line "  Forward Port:           ${B}8000${N}"
  line "  Block Common Exploits:  ✓"
  line "Καρτέλα ${B}SSL${N}"
  line "  SSL Certificate: ${B}Request a new SSL Certificate${N}"
  line "  Force SSL ✓ · HTTP/2 Support ✓ · HSTS Enabled ✓ · αποδοχή των όρων Let's Encrypt"
  line "Καρτέλα ${B}Advanced${N} -> Custom Nginx Configuration:"
  line "  ${B}client_max_body_size 60m;${N}"
  line ""
  line "Πάτα ${B}Save${N}. ${D}Αν το πιστοποιητικό αποτύχει: έλεγξε ότι το DNS δείχνει εδώ και ότι η 80 είναι ανοιχτή.${N}"
}

npm_instructions_ntfy() {
  todo "Proxy Host για το ntfy στο Nginx Proxy Manager"
  line "Hosts -> Proxy Hosts -> ${B}Add Proxy Host${N}"
  line ""
  line "Καρτέλα ${B}Details${N}"
  line "  Domain Names:           ${B}${NTFY_DOMAIN%%:*}${N}"
  line "  Scheme:                 ${B}http${N}"
  line "  Forward Hostname / IP:  ${B}study-ntfy${N}"
  line "  Forward Port:           ${B}80${N}"
  line "  Block Common Exploits:  ✓"
  line "  Websockets Support:     ✓   ${Y}(απαραίτητο για τις ειδοποιήσεις)${N}"
  line "Καρτέλα ${B}SSL${N}"
  line "  Request a new SSL Certificate · Force SSL ✓ · HTTP/2 Support ✓"
  line "Καρτέλα ${B}Advanced${N} -> Custom Nginx Configuration:"
  line "  ${B}proxy_buffering off;${N}"
  line "  ${B}proxy_request_buffering off;${N}"
  line "  ${B}proxy_read_timeout 3m;${N}"
  line "  ${B}proxy_send_timeout 3m;${N}"
  line ""
  line "Πάτα ${B}Save${N}."
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
  local app_sub=${app_host%%.*} ntfy_sub=${ntfy_host%%.*}
  cat <<EOF

${B}==================================================================${N}
${B}  Κατάσταση εγκατάστασης${N}
${B}==================================================================${N}
  ${D}✓ εντάξει · ✗ εκκρεμεί · ? θέλει προσοχή / δεν ελέγχεται από εδώ${N}

$(mark "$S_CONT") ${B}0. Containers${N}   app: $H_APP · ntfy: $H_NTFY

$(mark "$S_DNS") ${B}1. DNS records${N}  (στον πάροχο του domain σου)
     Τύπος   Όνομα           Τιμή
     A       $(pad "$app_sub" 16)$ip
     A       $(pad "$ntfy_sub" 16)$ip
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

$(mark "$S_NPM") ${B}3. Nginx Proxy Manager -> Proxy Hosts${N}
     $app_host   -> http://study-app:8000    (Advanced: client_max_body_size 60m;)
     $ntfy_host  -> http://study-ntfy:80     (Websockets ✓, Advanced: proxy_buffering off; proxy_read_timeout 3m;)
     SSL: Request a new SSL Certificate · Force SSL · HTTP/2
     Τώρα:  $(mark "$S_HTTPS_APP") https://$APP_DOMAIN/healthz
            $(mark "$S_HTTPS_NTFY") https://$NTFY_DOMAIN/v1/health

$(mark unknown) ${B}4. Κινητό${N}  εφαρμογή ntfy -> server https://$NTFY_DOMAIN · χρήστης phone · topic $TOPIC

$(mark unknown) ${B}5. Dashboard${N}  https://$APP_DOMAIN -> Μαθήματα -> Νέο μάθημα (xlsx + ics) -> Αρχεία -> Ρυθμίσεις

$(mark unknown) ${B}6. Ασφάλεια δεδομένων${N}
     - Αντίγραφο του $DIR/.env σε ασφαλές μέρος (π.χ. password manager).
     - Backups και εκτός server:  rsync -a $(id -un)@$ip:$DIR/backups/ ./study-backups/

${D}Έλεγχος ξανά:      cd $DIR && ./install.sh --check
Update:            cd $DIR && git pull && ./install.sh
Αλλαγή ρυθμίσεων:  STUDY_RECONFIGURE=1 ./install.sh
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

step "1. Έλεγχος προαπαιτούμενων"
[ -f docker-compose.yml ] && [ -f Dockerfile ] || die "Τρέξε το script μέσα από τον φάκελο της εφαρμογής."
[ "$(id -u)" -ne 0 ] || warn "Τρέχεις ως root. Προτείνεται κανονικός χρήστης που ανήκει στο group docker."
command -v docker >/dev/null || die "Δεν βρέθηκε το docker. Εγκατάστασέ το: https://docs.docker.com/engine/install/"
docker compose version >/dev/null 2>&1 || die "Χρειάζεται docker compose v2 (το plugin 'docker compose')."
docker info >/dev/null 2>&1 || die "Ο χρήστης $(id -un) δεν έχει πρόσβαση στο Docker. Τρέξε: sudo usermod -aG docker $(id -un)  και ξανασυνδέσου."
command -v curl >/dev/null || command -v wget >/dev/null || warn "Δεν υπάρχει curl ή wget: δεν θα μπορώ να ελέγξω το HTTPS."
ok "Docker $(docker version -f '{{.Server.Version}}'), $(docker compose version --short 2>/dev/null || echo compose v2)"
ok "Φάκελος εγκατάστασης: $DIR"

REUSE=no
if [ -f "$ENV_FILE" ] && [ -n "$(env_get SECRET_KEY)" ] && [ "${STUDY_RECONFIGURE-}" != 1 ]; then
  step "Υπάρχουσα εγκατάσταση"
  info "Βρέθηκε .env από προηγούμενη εγκατάσταση."
  if confirm "Να κρατηθούν οι ρυθμίσεις του (μόνο build/επανεκκίνηση);" y; then REUSE=yes; fi
fi

detect_ips

if [ "$REUSE" = yes ]; then
  load_saved_settings
else
  # ------------------------------------------------------------ 2-3. domains + DNS
  step "2. Domain της εφαρμογής"
  if [ -n "$PUBLIC_IP" ]; then info "Η δημόσια IP αυτού του server είναι ${B}$PUBLIC_IP${N}."; fi
  info "Διάλεξε ένα subdomain για το dashboard, π.χ. study.example.com"
  ask STUDY_APP_DOMAIN "Domain της εφαρμογής"
  APP_DOMAIN=${STUDY_APP_DOMAIN#*://}; APP_DOMAIN=${APP_DOMAIN%%/*}
  [[ $APP_DOMAIN =~ ^[A-Za-z0-9.-]+(:[0-9]+)?$ ]] || die "Μη έγκυρο domain: $APP_DOMAIN"
  guide "DNS: $APP_DOMAIN" check_dns_app dns_instructions_app || true

  step "3. Domain του ntfy (ειδοποιήσεις)"
  info "Ένα δεύτερο subdomain, π.χ. ntfy.example.com. Σε αυτό θα συνδέεται το κινητό."
  ask STUDY_NTFY_DOMAIN "Domain του ntfy"
  NTFY_DOMAIN=${STUDY_NTFY_DOMAIN#*://}; NTFY_DOMAIN=${NTFY_DOMAIN%%/*}
  [[ $NTFY_DOMAIN =~ ^[A-Za-z0-9.-]+(:[0-9]+)?$ ]] || die "Μη έγκυρο domain: $NTFY_DOMAIN"
  [ "$APP_DOMAIN" != "$NTFY_DOMAIN" ] || die "Η εφαρμογή και το ntfy χρειάζονται διαφορετικά domains."
  guide "DNS: $NTFY_DOMAIN" check_dns_ntfy dns_instructions_ntfy || true

  # ------------------------------------------------------------ 4. θύρες
  step "4. Θύρες 80 και 443"
  ports_instructions
  pause "Όταν οι θύρες είναι ανοιχτές, πάτα [Enter] (θα επιβεβαιωθεί στο βήμα του HTTPS): "

  # ------------------------------------------------------------ 5. λογαριασμοί και ρυθμίσεις
  step "5. Λογαριασμός dashboard"
  ask STUDY_ADMIN_USER "Όνομα χρήστη" admin
  ask_secret STUDY_ADMIN_PASSWORD "Κωδικός (τουλάχιστον 10 χαρακτήρες)" 10 no

  step "6. Ειδοποιήσεις"
  ask STUDY_NTFY_TOPIC "Topic (όνομα «καναλιού» ειδοποιήσεων)" study
  [[ $STUDY_NTFY_TOPIC =~ ^[A-Za-z0-9_-]{1,64}$ ]] || die "Το topic επιτρέπει μόνο λατινικά, αριθμούς, - και _."
  TOPIC=$STUDY_NTFY_TOPIC
  info "${D}Με αυτόν τον κωδικό θα συνδεθεί η εφαρμογή ntfy του κινητού (χρήστης: phone).${N}"
  ask_secret STUDY_PHONE_PASSWORD "Κωδικός για το κινητό" 8 yes
  ask STUDY_TZ "Ζώνη ώρας" Europe/Athens
  ask STUDY_MAX_UPLOAD_MB "Μέγιστο μέγεθος αρχείου (MB)" 50
  [[ $STUDY_MAX_UPLOAD_MB =~ ^[0-9]+$ ]] || die "Το μέγεθος πρέπει να είναι αριθμός."

  step "7. Αποθήκευση δεδομένων"
  info "Πού να αποθηκεύονται βάση, PDF και χρήστες ntfy; (ή γράψε 'volumes' για docker volumes)"
  ask STUDY_DATA "Φάκελος δεδομένων" "$DIR/data"
  if [ "$STUDY_DATA" = volumes ]; then DATA_DIR=""; else
    DATA_DIR=$STUDY_DATA; [[ $DATA_DIR = /* ]] || DATA_DIR="$DIR/$DATA_DIR"; DATA_DIR=${DATA_DIR%/}
  fi

  # ------------------------------------------------------------ 8. δίκτυο NPM
  step "8. Σύνδεση με τον Nginx Proxy Manager"
  NPM=${STUDY_NPM_CONTAINER-}
  if [ -z "$NPM" ]; then
    NPM=$(docker ps --format '{{.Names}}\t{{.Image}}' | awk -F'\t' 'tolower($2) ~ /nginx-proxy-manager/ {print $1; exit}')
  fi
  default_net=npm_proxy
  if [ -n "$NPM" ] && [ "$NPM" != "-" ]; then
    ok "Βρέθηκε το container του NPM: $NPM"
    for n in $(networks_of "$NPM"); do
      if [ "$n" != bridge ] && [ "$n" != host ]; then default_net=$n; break; fi
    done
  else
    warn "Δεν βρέθηκε container του Nginx Proxy Manager."
    ask STUDY_NPM_CONTAINER "Όνομα του container του NPM (ή - αν θα το συνδέσεις αργότερα)" "-"
    NPM=$STUDY_NPM_CONTAINER
  fi
  ask STUDY_PROXY_NETWORK "Κοινό docker network με το NPM" "$default_net"
  PROXY_NETWORK=$STUDY_PROXY_NETWORK
fi

if docker network inspect "$PROXY_NETWORK" >/dev/null 2>&1; then
  ok "Το δίκτυο $PROXY_NETWORK υπάρχει."
else
  docker network create "$PROXY_NETWORK" >/dev/null
  ok "Δημιουργήθηκε το δίκτυο $PROXY_NETWORK."
fi
if [ "$REUSE" = no ] && [ -n "${NPM-}" ] && [ "$NPM" != "-" ]; then
  if [[ " $(networks_of "$NPM") " == *" $PROXY_NETWORK "* ]]; then
    ok "Το NPM είναι ήδη στο $PROXY_NETWORK."
  else
    docker network connect "$PROXY_NETWORK" "$NPM"
    ok "Το NPM ($NPM) συνδέθηκε στο $PROXY_NETWORK."
  fi
fi

# ---------------------------------------------------------------- 9. .env, φάκελοι, build

step "9. Εγκατάσταση εφαρμογής"
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
TRUSTED_PROXY_HOPS='1'
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
  step "10. Nginx Proxy Manager: εφαρμογή"
  guide "HTTPS: $APP_DOMAIN" check_https_app npm_instructions_app || true

  step "11. Nginx Proxy Manager: ntfy"
  guide "HTTPS: $NTFY_DOMAIN" check_https_ntfy npm_instructions_ntfy || true

  # ------------------------------------------------------------ 12. κινητό
  step "12. Κινητό"
  phone_instructions
  if check_https_ntfy; then
    pause "Όταν κάνεις Subscribe στο κινητό, πάτα [Enter] να σου στείλω δοκιμαστική ειδοποίηση: "
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
    info "Όταν ολοκληρώσεις το βήμα 11, κάνε τη σύνδεση στο κινητό και στείλε δοκιμαστική"
    info "από το dashboard: Ρυθμίσεις -> Δοκιμαστική ειδοποίηση."
  fi
else
  send_test_notification "future-me-will-study · Η ενημέρωση ολοκληρώθηκε" && ok "Στάλθηκε δοκιμαστική ειδοποίηση." || \
    warn "Η δοκιμαστική ειδοποίηση απέτυχε. Logs: docker compose logs app ntfy"
fi

# ---------------------------------------------------------------- 13. backup

step "13. Backup"
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

# ---------------------------------------------------------------- 14. τέλος

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
  line "${D}Κράτα αντίγραφο του $DIR/.env σε ασφαλές μέρος (π.χ. password manager).${N}"
  line "${D}Έλεγχος οποτεδήποτε: ./install.sh --check · Update: git pull && ./install.sh${N}"
else
  step "Η εγκατάσταση ολοκληρώθηκε, αλλά μένουν εκκρεμότητες"
  print_next_steps
  info "Όταν τις κάνεις:  ${B}cd $DIR && ./install.sh --check${N}"
fi
printf '\n%sΗ κατάσταση αποθηκεύτηκε στο %s/next-steps.txt%s\n' "$D" "$DIR" "$N"
