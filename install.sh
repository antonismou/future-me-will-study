#!/usr/bin/env bash
# Εγκατάσταση του future-me-will-study σε Linux server, κάτω από τον τρέχοντα χρήστη.
#
#   git clone <repo> ~/study && cd ~/study && ./install.sh
#   ./install.sh --check      (μόνο έλεγχος DNS, θυρών, HTTPS και οδηγίες)
#
# Μπορεί να ξανατρέξει με ασφάλεια (π.χ. για αλλαγή ρυθμίσεων ή μετά από update).
# Για αυτοματοποίηση, κάθε ερώτηση παίρνει τιμή από env var (STUDY_APP_DOMAIN, STUDY_ADMIN_PASSWORD, ...).
set -Eeuo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"
ENV_FILE="$DIR/.env"
OVERRIDE="$DIR/docker-compose.override.yml"
TTY=/dev/tty

if [ -t 1 ]; then
  B=$'\e[1m'; D=$'\e[2m'; R=$'\e[31m'; G=$'\e[32m'; Y=$'\e[33m'; N=$'\e[0m'
else
  B=; D=; R=; G=; Y=; N=
fi
step() { printf '\n%s==> %s%s\n' "$B" "$*" "$N"; }
info() { printf '    %s\n' "$*"; }
ok()   { printf '    %s✓%s %s\n' "$G" "$N" "$*"; }
warn() { printf '    %s!%s %s\n' "$Y" "$N" "$*"; }
die()  { printf '\n%sΣφάλμα:%s %s\n' "$R" "$N" "$*" >&2; exit 1; }
trap 'die "η εντολή στη γραμμή $LINENO απέτυχε (δες το μήνυμα παραπάνω)."' ERR

# ---------------------------------------------------------------- βοηθητικά

ask() { # ask VAR "ερώτηση" [default]
  local var=$1 q=$2 def=${3-} ans=""
  [ -n "${!var-}" ] && return 0
  while :; do
    ans=""
    if ! read -r -p "    $q${def:+ [$def]}: " ans 2>/dev/null <"$TTY"; then
      # Χωρίς τερματικό: default αν υπάρχει, αλλιώς σταματάμε αντί να κολλήσουμε.
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
  [ -r "$TTY" ] || die "Χρειάζεται τερματικό για την ερώτηση «$q» (ή όρισε τη μεταβλητή $var)."
  while :; do
    a=""; b=""
    if [ "$rnd" = yes ]; then read -r -s -p "    $q (Enter = τυχαίος): " a <"$TTY" || true
    else read -r -s -p "    $q: " a <"$TTY" || true; fi
    echo
    if [ -z "$a" ] && [ "$rnd" = yes ]; then a=$(randstr 20); printf -v "${var}_GENERATED" '%s' yes; break; fi
    if [ ${#a} -lt "$min" ]; then warn "Τουλάχιστον $min χαρακτήρες."; continue; fi
    read -r -s -p "    Ξανά για επιβεβαίωση: " b <"$TTY" || true; echo
    [ "$a" = "$b" ] && break
    warn "Δεν ταιριάζουν, δοκίμασε ξανά."
  done
  printf -v "$var" '%s' "$a"
}

confirm() { # confirm "ερώτηση" y|n
  local q=$1 def=${2:-y} ans hint
  if [ "${STUDY_ASSUME_YES-}" = 1 ]; then [ "$def" = y ]; return; fi
  [ "$def" = y ] && hint="Ν/ο" || hint="ν/Ο"
  read -r -p "    $q [$hint]: " ans <"$TTY" || true
  ans=${ans:-$def}
  case $ans in y*|Y*|ν*|Ν*) return 0 ;; *) return 1 ;; esac
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

# ---------------------------------------------------------------- έλεγχοι δικτύου και οδηγίες

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
  local body
  body=$(http_get "https://$APP_DOMAIN/healthz")
  if [[ $body == *'"ok":true'* ]]; then S_HTTPS_APP=ok; else S_HTTPS_APP=fail; fi
  body=$(http_get "https://$NTFY_DOMAIN/v1/health")
  if [[ $body == *healthy*true* ]]; then S_HTTPS_NTFY=ok; else S_HTTPS_NTFY=fail; fi
  if [ "$S_HTTPS_APP" = ok ] && [ "$S_HTTPS_NTFY" = ok ]; then S_NPM=ok; else S_NPM=fail; fi
}

print_next_steps() {
  local ip=${PUBLIC_IP:-"<δημόσια IP του server>"} app_host=${APP_DOMAIN%%:*} ntfy_host=${NTFY_DOMAIN%%:*}
  local app_sub=${app_host%%.*} ntfy_sub=${ntfy_host%%.*}
  cat <<EOF

${B}==================================================================${N}
${B}  Τι μένει να κάνεις με το χέρι${N}
${B}==================================================================${N}
  ${D}✓ ελέγχθηκε και είναι εντάξει · ✗ εκκρεμεί · ? θέλει προσοχή / δεν ελέγχεται από εδώ${N}

$(mark "$S_CONT") ${B}0. Containers${N}   app: $H_APP · ntfy: $H_NTFY

$(mark "$S_DNS") ${B}1. DNS records${N}  (στον πάροχο του domain σου: Cloudflare, Papaki, Namecheap, ...)
     Τύπος   Όνομα           Τιμή
     A       $(pad "$app_sub" 16)$ip
     A       $(pad "$ntfy_sub" 16)$ip
     ${D}Το «Όνομα» είναι το πρώτο κομμάτι του domain. Σε μερικούς παρόχους γράφεις ολόκληρο το $app_host.
     Αν ο server έχει και IPv6, πρόσθεσε και αντίστοιχα AAAA records. TTL: Auto ή 300.
     Cloudflare: «DNS only» (γκρι σύννεφο), τουλάχιστον μέχρι να εκδοθούν τα πιστοποιητικά.${N}
     Τώρα:  $(mark "$S_DNS_APP") $app_host -> ${DNS_APP:-δεν υπάρχει ακόμα}
            $(mark "$S_DNS_NTFY") $ntfy_host -> ${DNS_NTFY:-δεν υπάρχει ακόμα}
EOF
  if [ "$S_DNS" = warn ]; then
    printf '     %sΚάποιο domain δείχνει σε διαφορετική IP από τη δημόσια (%s). Με Cloudflare proxy αυτό είναι αναμενόμενο.%s\n' "$Y" "$ip" "$N"
  fi
  printf '\n%s %s2. Θύρες 80 και 443 προς τον server%s\n' "$(mark "$S_PORTS")" "$B" "$N"
  if [ "$S_PORTS" = ok ]; then
    printf '     Ο Nginx Proxy Manager ακούει στις 80/443 σε αυτόν τον server.\n'
  else
    printf '     %sΔεν ακούει τίποτα στις 80/443 εδώ. Τρέχει ο Nginx Proxy Manager;%s\n' "$R" "$N"
  fi
  if [ "$BEHIND_NAT" = yes ]; then
    cat <<EOF
     Ο server έχει ιδιωτική IP ($LOCAL_IP), δηλαδή είναι πίσω από router. Στο router:
       Port forwarding   TCP 80   -> $LOCAL_IP:80
       Port forwarding   TCP 443  -> $LOCAL_IP:443
     ${D}Αν η IP της σύνδεσής σου αλλάζει, βάλε Dynamic DNS (από το router ή π.χ. Cloudflare DDNS).${N}
EOF
  else
    cat <<EOF
     Άνοιξε TCP 80 και 443 στο firewall του παρόχου (π.χ. Hetzner Firewall, Oracle Security List,
     AWS Security Group). Αν έχεις ufw στον server:   sudo ufw allow 80,443/tcp
EOF
  fi
  cat <<EOF

$(mark "$S_NPM") ${B}3. Nginx Proxy Manager${N}  ->  Hosts -> Proxy Hosts -> Add Proxy Host (δύο φορές)

     ${B}$app_host${N}
       Details:   Scheme http · Forward Hostname ${B}study-app${N} · Forward Port ${B}8000${N}
                  Block Common Exploits ✓
       SSL:       Request a new SSL Certificate · Force SSL ✓ · HTTP/2 Support ✓ · HSTS Enabled ✓
       Advanced:  client_max_body_size 60m;

     ${B}$ntfy_host${N}
       Details:   Scheme http · Forward Hostname ${B}study-ntfy${N} · Forward Port ${B}80${N}
                  Block Common Exploits ✓ · Websockets Support ✓ ${D}(απαραίτητο)${N}
       SSL:       Request a new SSL Certificate · Force SSL ✓ · HTTP/2 Support ✓
       Advanced:  proxy_buffering off;
                  proxy_request_buffering off;
                  proxy_read_timeout 3m;
                  proxy_send_timeout 3m;

     ${D}Το πιστοποιητικό εκδίδεται μόνο αφού ολοκληρωθούν τα βήματα 1 και 2.${N}
     Τώρα:  $(mark "$S_HTTPS_APP") https://$APP_DOMAIN/healthz
            $(mark "$S_HTTPS_NTFY") https://$NTFY_DOMAIN/v1/health

$(mark unknown) ${B}4. Κινητό (Android)${N}
     1. Εγκατάσταση της εφαρμογής «ntfy» από Google Play ή F-Droid.
     2. Settings -> Manage users -> Add user
          Service URL: https://$NTFY_DOMAIN    Username: phone    Password: ο κωδικός του κινητού
     3. + -> Topic name: ${B}$TOPIC${N} -> ✓ Use another server -> https://$NTFY_DOMAIN -> Subscribe
     4. Ρυθμίσεις Android -> Εφαρμογές -> ntfy -> Μπαταρία -> «Χωρίς περιορισμούς»,
        ώστε οι ειδοποιήσεις να φτάνουν στην ώρα τους.

$(mark unknown) ${B}5. Πρώτη ρύθμιση στο dashboard${N}   https://$APP_DOMAIN
     1. Σύνδεση με τον χρήστη του dashboard.
     2. Μαθήματα -> Νέο μάθημα: όνομα, ημερομηνία εξέτασης, xlsx πλάνου (και .ics αν υπάρχει).
     3. Αρχεία: ανέβασε τα PDF ανά κατηγορία (π.χ. L01-L13 στις «Ημερήσιες σημειώσεις»).
     4. Ρυθμίσεις: ώρες ειδοποιήσεων -> Αποθήκευση -> «Δοκιμαστική ειδοποίηση».

$(mark unknown) ${B}6. Ασφάλεια δεδομένων${N}
     - Κράτα αντίγραφο του $DIR/.env σε ασφαλές μέρος (π.χ. password manager).
     - Αντέγραφε τα backups και εκτός server, π.χ. από τον υπολογιστή σου:
         rsync -a $(id -un)@$ip:$DIR/backups/ ./study-backups/

${B}Μόλις κάνεις τα βήματα 1-3, έλεγξε ξανά με:${N}   cd $DIR && ./install.sh --check

${D}Update:            cd $DIR && git pull && ./install.sh
Αλλαγή ρυθμίσεων:  STUDY_RECONFIGURE=1 ./install.sh
Backup τώρα:       ./backup.sh
Logs:              docker compose logs -f app${N}
EOF
}

write_next_steps_file() {
  ( B=; D=; R=; G=; Y=; N=; print_next_steps ) >"$DIR/next-steps.txt"
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

# ---------------------------------------------------------------- 1. έλεγχοι

step "Έλεγχος προαπαιτούμενων"
[ -f docker-compose.yml ] && [ -f Dockerfile ] || die "Τρέξε το script μέσα από τον φάκελο της εφαρμογής."
[ "$(id -u)" -ne 0 ] || warn "Τρέχεις ως root. Προτείνεται κανονικός χρήστης που ανήκει στο group docker."
command -v docker >/dev/null || die "Δεν βρέθηκε το docker. Εγκατάστασέ το: https://docs.docker.com/engine/install/"
docker compose version >/dev/null 2>&1 || die "Χρειάζεται docker compose v2 (το plugin 'docker compose')."
docker info >/dev/null 2>&1 || die "Ο χρήστης $(id -un) δεν έχει πρόσβαση στο Docker. Τρέξε: sudo usermod -aG docker $(id -un)  και ξανασυνδέσου."
ok "Docker $(docker version -f '{{.Server.Version}}'), $(docker compose version --short 2>/dev/null || echo compose v2)"
ok "Φάκελος εγκατάστασης: $DIR"

# ---------------------------------------------------------------- 2. ρυθμίσεις

REUSE=no
if [ -f "$ENV_FILE" ] && [ -n "$(env_get SECRET_KEY)" ] && [ "${STUDY_RECONFIGURE-}" != 1 ]; then
  step "Υπάρχουσα εγκατάσταση"
  info "Βρέθηκε .env από προηγούμενη εγκατάσταση."
  if confirm "Να κρατηθούν οι ρυθμίσεις του (μόνο build/επανεκκίνηση);" y; then REUSE=yes; fi
fi

if [ "$REUSE" = yes ]; then
  load_saved_settings
else
  step "Βασικές ρυθμίσεις"
  detect_ips
  if [ -n "$PUBLIC_IP" ]; then info "Δημόσια IP του server: ${B}$PUBLIC_IP${N}"; fi
  if [ "$BEHIND_NAT" = yes ]; then info "Τοπική IP: $LOCAL_IP (πίσω από router: θα χρειαστεί port forwarding 80/443)"; fi
  info "${D}Θα χρειαστείς δύο subdomains που δείχνουν σε αυτή την IP. Οδηγίες για τα DNS records δίνονται στο τέλος.${N}"
  ask STUDY_APP_DOMAIN "Domain της εφαρμογής (π.χ. study.example.com)"
  ask STUDY_NTFY_DOMAIN "Domain του ntfy (π.χ. ntfy.example.com)"
  APP_DOMAIN=${STUDY_APP_DOMAIN#*://}; APP_DOMAIN=${APP_DOMAIN%%/*}
  NTFY_DOMAIN=${STUDY_NTFY_DOMAIN#*://}; NTFY_DOMAIN=${NTFY_DOMAIN%%/*}
  [[ $APP_DOMAIN =~ ^[A-Za-z0-9.-]+(:[0-9]+)?$ ]] || die "Μη έγκυρο domain: $APP_DOMAIN"
  [[ $NTFY_DOMAIN =~ ^[A-Za-z0-9.-]+(:[0-9]+)?$ ]] || die "Μη έγκυρο domain: $NTFY_DOMAIN"
  [ "$APP_DOMAIN" != "$NTFY_DOMAIN" ] || die "Η εφαρμογή και το ntfy χρειάζονται διαφορετικά domains."
  for d in "$APP_DOMAIN" "$NTFY_DOMAIN"; do
    if command -v getent >/dev/null && ! getent hosts "${d%%:*}" >/dev/null; then
      warn "Το $d δεν επιλύεται ακόμα από DNS. Μπορείς να συνεχίσεις, αλλά το HTTPS θα αποτύχει μέχρι να οριστεί."
    fi
  done

  step "Λογαριασμός dashboard"
  ask STUDY_ADMIN_USER "Όνομα χρήστη" admin
  ask_secret STUDY_ADMIN_PASSWORD "Κωδικός (τουλάχιστον 10 χαρακτήρες)" 10 no

  step "Ειδοποιήσεις (ntfy)"
  ask STUDY_NTFY_TOPIC "Topic" study
  [[ $STUDY_NTFY_TOPIC =~ ^[A-Za-z0-9_-]{1,64}$ ]] || die "Το topic επιτρέπει μόνο λατινικά, αριθμούς, - και _."
  TOPIC=$STUDY_NTFY_TOPIC
  info "${D}Με αυτόν τον κωδικό θα συνδεθεί η εφαρμογή ntfy του κινητού (χρήστης: phone).${N}"
  ask_secret STUDY_PHONE_PASSWORD "Κωδικός για το κινητό" 8 yes

  step "Γενικά"
  ask STUDY_TZ "Ζώνη ώρας" Europe/Athens
  ask STUDY_MAX_UPLOAD_MB "Μέγιστο μέγεθος αρχείου (MB)" 50
  [[ $STUDY_MAX_UPLOAD_MB =~ ^[0-9]+$ ]] || die "Το μέγεθος πρέπει να είναι αριθμός."

  step "Αποθήκευση δεδομένων"
  info "Πού να αποθηκεύονται βάση, PDF και χρήστες ntfy;"
  info "  - ένας φάκελος (προτείνεται, π.χ. $DIR/data)"
  info "  - ή γράψε 'volumes' για docker volumes"
  ask STUDY_DATA "Φάκελος δεδομένων" "$DIR/data"
  if [ "$STUDY_DATA" = volumes ]; then DATA_DIR=""; else
    DATA_DIR=$STUDY_DATA; [[ $DATA_DIR = /* ]] || DATA_DIR="$DIR/$DATA_DIR"; DATA_DIR=${DATA_DIR%/}
  fi

  # ------------------------------------------------------------ δίκτυο NPM
  step "Σύνδεση με Nginx Proxy Manager"
  NPM=${STUDY_NPM_CONTAINER-}
  if [ -z "$NPM" ]; then
    NPM=$(docker ps --format '{{.Names}}\t{{.Image}}' | awk -F'\t' 'tolower($2) ~ /nginx-proxy-manager/ {print $1; exit}')
  fi
  default_net=npm_proxy
  if [ -n "$NPM" ] && [ "$NPM" != "-" ]; then
    ok "Βρέθηκε το container του NPM: $NPM"
    nets=$(networks_of "$NPM")
    info "Δίκτυα του NPM: $nets"
    for n in $nets; do
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

# ---------------------------------------------------------------- 3. δίκτυο

step "Docker network"
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

# ---------------------------------------------------------------- 4. .env και φάκελοι

if [ "$REUSE" = no ]; then
  step "Αρχείο ρυθμίσεων (.env)"
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
    # Τα containers τρέχουν ως uid 10001 (app) και 10002 (ntfy): δίνουμε δικαιώματα μέσω docker, χωρίς sudo.
    docker run --rm -v "$DATA_DIR/app:/a" -v "$DATA_DIR/ntfy:/n" busybox:1.36 \
      sh -c 'chown -R 10001:10001 /a && chown -R 10002:10002 /n' >/dev/null
    ok "Δεδομένα στο $DATA_DIR (app/, ntfy/)."
  else
    rm -f "$OVERRIDE"
    mkdir -p "$DIR/backups"
    ok "Δεδομένα σε docker volumes (study_study-data, study_ntfy-data)."
  fi
fi

# ---------------------------------------------------------------- 5. build + κωδικός

step "Build της εφαρμογής"
docker compose build --pull app
ok "Το image χτίστηκε."

if [ "$REUSE" = no ]; then
  HASH=$(printf '%s' "$STUDY_ADMIN_PASSWORD" | docker compose run --rm -T --no-deps --entrypoint python app -c \
    'import sys, bcrypt; print(bcrypt.hashpw(sys.stdin.buffer.read()[:72], bcrypt.gensalt(12)).decode())')
  [[ $HASH == \$2* ]] || die "Δεν δημιουργήθηκε hash κωδικού."
  env_set ADMIN_PASSWORD_HASH "$HASH"
  ok "Ο κωδικός αποθηκεύτηκε ως bcrypt hash."
fi

# ---------------------------------------------------------------- 6. εκκίνηση

step "Εκκίνηση"
docker compose up -d --build --remove-orphans
wait_healthy study-ntfy

# ---------------------------------------------------------------- 7. ntfy χρήστες

step "Χρήστες ntfy"
if ! ntfy_user_exists studyapp; then
  docker compose exec -T -e NTFY_PASSWORD="$(randstr 32)" ntfy ntfy user add studyapp >/dev/null 2>&1
  ok "Χρήστης studyapp (για την αποστολή)."
fi
docker compose exec -T ntfy ntfy access studyapp "$TOPIC" write-only >/dev/null 2>&1
if [ -z "$(env_get NTFY_TOKEN)" ]; then
  TOKEN=$(docker compose exec -T ntfy ntfy token add studyapp 2>&1 | grep -oE -m1 'tk_[A-Za-z0-9]+' || true)
  [ -n "$TOKEN" ] || die "Δεν δημιουργήθηκε token στο ntfy."
  env_set NTFY_TOKEN "$TOKEN"
  ok "Token αποστολής δημιουργήθηκε και γράφτηκε στο .env."
else
  ok "Υπάρχει ήδη token αποστολής."
fi
if [ "$REUSE" = no ]; then
  if ntfy_user_exists phone; then
    docker compose exec -T -e NTFY_PASSWORD="$STUDY_PHONE_PASSWORD" ntfy ntfy user change-pass phone >/dev/null 2>&1
    ok "Ο κωδικός του χρήστη phone ενημερώθηκε."
  else
    docker compose exec -T -e NTFY_PASSWORD="$STUDY_PHONE_PASSWORD" ntfy ntfy user add phone >/dev/null 2>&1
    ok "Χρήστης phone (για το κινητό, μόνο ανάγνωση)."
  fi
fi
docker compose exec -T ntfy ntfy access phone "$TOPIC" read-only >/dev/null 2>&1

# Νέο token -> ξαναδημιουργία του app για να το διαβάσει.
docker compose up -d app >/dev/null 2>&1
wait_healthy study-app

# ---------------------------------------------------------------- 8. δοκιμή

step "Δοκιμαστική ειδοποίηση"
if docker compose exec -T app python - <<'PY'
from app.db import connect
from app.settings_store import get_settings
from app import notify
conn = connect()
s = get_settings(conn)
notify.publish(s, {"title": "Η εγκατάσταση ολοκληρώθηκε", "message": "Οι ειδοποιήσεις φτάνουν σωστά στο κινητό.",
                   "tags": ["white_check_mark"]})
PY
then ok "Στάλθηκε στο topic '$TOPIC'. Θα τη δεις μόλις συνδέσεις το κινητό."
else warn "Η αποστολή απέτυχε. Δες: docker compose logs app ntfy"; fi

# ---------------------------------------------------------------- 9. backup

step "Backup"
chmod +x "$DIR/backup.sh"
if command -v crontab >/dev/null; then
  current_cron=$(crontab -l 2>/dev/null || true)
  if [[ $current_cron == *"# study-backup"* ]]; then
    ok "Το καθημερινό backup είναι ήδη προγραμματισμένο."
  elif confirm "Να προγραμματιστεί καθημερινό backup στις 03:30;" y; then
    line="30 3 * * * $DIR/backup.sh >>$DIR/backups/backup.log 2>&1 # study-backup"
    { [ -n "$current_cron" ] && printf '%s
' "$current_cron"; echo "$line"; } | crontab -
    ok "Backup κάθε μέρα στις 03:30 στο $DIR/backups (κρατούνται τα 14 τελευταία)."
  fi
else
  warn "Δεν υπάρχει crontab. Τρέχε χειροκίνητα: $DIR/backup.sh"
fi

# ---------------------------------------------------------------- 10. τι μένει

step "Έλεγχος δικτύου, DNS και HTTPS"
run_checks
printf '\n%s%sΗ εγκατάσταση ολοκληρώθηκε.%s Η εφαρμογή και το ntfy τρέχουν σε αυτόν τον server.\n' "$B" "$G" "$N"
print_next_steps
write_next_steps_file
if [ "$REUSE" = no ] && [ "${STUDY_PHONE_PASSWORD_GENERATED-}" = yes ]; then
  printf '\n%sΚωδικός κινητού (χρήστης phone): %s%s   <- σημείωσέ τον, δεν αποθηκεύεται πουθενά.\n' "$B" "$STUDY_PHONE_PASSWORD" "$N"
fi
printf '\n%sΟι οδηγίες αποθηκεύτηκαν στο %s/next-steps.txt%s\n' "$D" "$DIR" "$N"
