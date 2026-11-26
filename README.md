# 📚 future-me-will-study

Μικρή self-hosted εφαρμογή που οργανώνει το διάβασμα ανά μάθημα και στέλνει ειδοποιήσεις στο κινητό μέσω **ntfy**.

- **Dashboard** (mobile-first, ελληνικά): σημερινή εργασία, πρόοδος ανά εβδομάδα και συνολικά, «έμειναν πίσω», αλλαγή κατάστασης, σημειώσεις, αναβολή, αρχεία (PDF/εικόνες) ανά μάθημα και κατηγορία.
- **Ειδοποιήσεις**:
  - **πρωινή**: θέμα, εργασία και PDF της ημέρας με link, countdown εξέτασης, όσα έμειναν πίσω
  - **βραδινές**: μόνο αν η εργασία δεν είναι «Ναι», με το streak
  - **εβδομαδιαία σύνοψη**
  - κουμπιά **Έγινε / Σε εξέλιξη / Αναβολή**
- **Ρυθμίσεις από το dashboard**: ώρες και πλήθος, ημέρες, ζώνη ώρας, ntfy, προτεραιότητα, ώρες ησυχίας, on/off ανά τύπο, δοκιμαστική ειδοποίηση. Ισχύουν χωρίς restart.
- **Import/Export**:
  - εισαγωγή πλάνου από xlsx (μορφή `AHK_Study_Tracker.xlsx`), προαιρετικά και .ics για την αντιστοίχιση ημέρα → PDF
  - εξαγωγή πίσω στο **ίδιο** xlsx, με formulas, Dashboard, γράφημα και dropdown
  - εξαγωγή σε νέο xlsx ή CSV
- **Δυσκολίες**: ανά μάθημα, μαζεύει τις σημειώσεις και τις εργασίες που σημείωσες «Δύσκολο». Έχει «Το ξαναείδα» και εκτύπωση για την τελική επανάληψη.
- **Χρόνος μελέτης**:
  - χρονόμετρο σε κάθε εργασία, με ένδειξη στο πάνω μέρος όσο τρέχει
  - χειροκίνητη προσθήκη λεπτών
  - στατιστικά ανά μάθημα: σήμερα, 7 ημέρες, γράφημα 14 ημερών, ανά εβδομάδα πλάνου, ανά εργασία
  - ο χρόνος μπαίνει και στο CSV και στην εβδομαδιαία σύνοψη
  - ένα ξεχασμένο χρονόμετρο μετράει έως 6 ώρες
- **Εβδομαδιαίος στόχος**: ώρες μελέτης ανά μάθημα (Μάθημα → Ρυθμίσεις). Η πρόοδος Δευτέρα–Κυριακή φαίνεται στο «Σήμερα», στον «Χρόνο μελέτης» και στην εβδομαδιαία σύνοψη.
- **Ημερολόγιο**: ιδιωτικό link (Ρυθμίσεις → «Ημερολόγιο στο κινητό») με το πλάνο και τις εξετάσεις ως ολοήμερα γεγονότα, για Google Calendar, iPhone κ.λπ. Μόνο ανάγνωση. Το link μπορεί να αντικατασταθεί ή να καταργηθεί. Η Google ανανεώνει με καθυστέρηση ωρών.
- **Πλάνο από θέματα** (Μαθήματα → «Πλάνο από θέματα»): γράφεις θέματα με μέγεθος (`Θέμα | μικρό/μεσαίο/μεγάλο`), ημερομηνία εξέτασης (ή εκτίμηση), ημέρες μελέτης, ρεπό και ημέρες επανάληψης. Βλέπεις προεπισκόπηση και μετά δημιουργείται το μάθημα.
- **Ισορροπία μαθημάτων** (Μαθήματα → «Ισορροπία μαθημάτων»): δηλώνεις διαθέσιμες ώρες ανά ημέρα και το «Σήμερα» προτείνει πόσο χρόνο να δώσεις σε κάθε μάθημα, με προειδοποίηση αν δεν βγαίνει ο χρόνος. Η εκτίμηση βασίζεται στη διάρκεια της μεσαίας εργασίας (ρυθμίσεις μαθήματος) και στο μέγεθος κάθε εργασίας.
- **Πρόταση αναπρογραμματισμού** (Ισορροπία → «Πρόταση αναπρογραμματισμού»): προτείνει νέες ημερομηνίες για τις καθυστερημένες εργασίες και όσες δεν χωράνε στις ώρες σου, με προτεραιότητα στην πιο κοντινή εξέταση και χωρίς να αλλάζει η σειρά τους. Τίποτα δεν αλλάζει αν δεν το δεχτείς (όλα ή όσα διαλέξεις), και η τελευταία αποδοχή αναιρείται.
- Υποστηρίζει **πολλά μαθήματα**.

## Αρχιτεκτονική

```
κινητό (ntfy app) ──https──► NPM ──► study-ntfy  (binwiederhier/ntfy, deny-all + χρήστες/tokens)
       │  κουμπιά/links             ▲ http://ntfy (εσωτερικό δίκτυο)
       └────────https──► NPM ──► study-app   (FastAPI + SQLite + scheduler thread, uid 10001)
                                     └── volume study-data: /data/study.db, /data/files, /data/templates
```

- **Stack**: Python 3.12, FastAPI, Jinja2 (server-rendered, χωρίς CDN), SQLite (WAL), openpyxl.
- **Scheduler**: ένα thread που κάνει έλεγχο κάθε 30″. Διαβάζει τις ρυθμίσεις από τη βάση σε κάθε έλεγχο.
- **Χωρίς διπλές ειδοποιήσεις**:
  - Κάθε ειδοποίηση έχει μοναδικό κλειδί, π.χ. `morning:<μάθημα>:<ημερομηνία>:<εργασία>`, και γράφεται στο `notification_log` **πριν** σταλεί. Ένα restart δεν ξαναστέλνει τίποτα.
  - Αν ο server ήταν κάτω την ώρα μιας ειδοποίησης, αυτή στέλνεται όταν ξανασηκωθεί, μόνο μέσα στο **παράθυρο ανοχής** (default 60′). Αλλιώς παραλείπεται.
  - Μια αποτυχημένη αποστολή, π.χ. όταν το ntfy είναι κάτω, ξαναδοκιμάζεται έως 3 φορές.
- **Ζώνη ώρας**: `zoneinfo`. Οι αλλαγές θερινής/χειμερινής ώρας χειρίζονται σωστά, με test.
- **Κουμπιά ntfy**: κάνουν `POST https://study.../a/<token>`. Το token είναι HMAC-SHA256 για εργασία + ενέργεια + ημερομηνία + λήξη (3 μέρες). Η «Αναβολή» δεν μετακινεί την εργασία δεύτερη φορά αν πατηθεί ξανά.
- **Link του PDF στην ειδοποίηση**: υπογεγραμμένο, ισχύει 36 ώρες και ανοίγει χωρίς login. Όλα τα άλλα αρχεία θέλουν login.

## Γρήγορη εγκατάσταση με script (προτείνεται)

Προϋποθέσεις:
- Docker με compose v2.
- Ο χρήστης σου ανήκει στο group `docker`. Αν όχι: `sudo usermod -aG docker $USER` και ξανασυνδέσου.
- Domain στο **Cloudflare**.
- **Nginx Proxy Manager** (με το web UI του) σε Docker, στον ίδιο server.

Τα DNS records, τα Proxy Hosts κ.λπ. **δεν** χρειάζεται να υπάρχουν από πριν: το script σου λέει πότε και πώς να τα φτιάξεις.

```bash
git clone <repo> ~/study        # ή: scp/tar του κώδικα σε ~/study
cd ~/study
./install.sh
```

Το script είναι **οδηγός βήμα-βήμα**. Πρώτα κάνει όλες τις ερωτήσεις: καθεμία έχει εξήγηση, παράδειγμα ή προτεινόμενη τιμή, και αν γράψεις κάτι λάθος σου λέει τι φταίει και ξαναρωτά. Στο τέλος δείχνει σύνοψη για να διορθώσεις ό,τι θες. Όπου υπάρχει `➜` περιμένει κάτι από εσένα και γράφει τι. Στους κωδικούς δεν εμφανίζεται τίποτα καθώς πληκτρολογείς.

Μετά ακολουθούν τα βήματα. Ό,τι γίνεται έξω από το script σημειώνεται με «ΚΑΝΕ ΤΩΡΑ». Μόλις το κάνεις, πατάς Enter και ελέγχεται (`?` ξαναδείχνει τις οδηγίες, `s` το αφήνει για αργότερα).

| Βήμα | Τι κάνεις εσύ | Τι ελέγχει το script |
|---|---|---|
| Ερωτήσεις | Δύο subdomains, Cloudflare «DNS only» ή «Proxied», χρήστης/κωδικός dashboard, topic, κωδικός κινητού, ζώνη ώρας, μέγεθος αρχείων, φάκελος δεδομένων, container και network του NPM (διαλέγεις από λίστα) | Τη μορφή κάθε τιμής |
| 1. Cloudflare | Δύο **A records** (και SSL/TLS «Full (strict)» αν είναι Proxied) | Ότι τα domains επιλύονται σωστά |
| 2. Θύρες | Port forwarding 80/443 στο router ή άνοιγμα στο firewall | Ότι ο NPM ακούει στις 80/443 |
| 3. Εγκατάσταση | Τίποτα: γράφει το `.env` (600, τυχαίο `SECRET_KEY`, bcrypt hash), χτίζει, ξεκινά και φτιάχνει χρήστες και token στο ntfy | Ότι τα containers είναι healthy |
| 4. NPM | Δύο Proxy Hosts πεδίο-πεδίο (σε Proxied και API token του Cloudflare για DNS Challenge) | Ότι το `https://…` απαντά, αλλιώς λέει την πιθανή αιτία |
| 5. Κινητό | Ρύθμιση της εφαρμογής ntfy | Στέλνει δοκιμαστική ειδοποίηση και ρωτά αν ήρθε |
| 6. Backup | Αν θέλεις καθημερινό backup στο cron | – |

Στο τέλος, αν όλα είναι ✓, σου δίνει τα πρώτα βήματα στο dashboard. Αλλιώς δείχνει μόνο ό,τι έμεινε ανοιχτό. Η κατάσταση αποθηκεύεται στο `next-steps.txt`. Αργότερα ξαναελέγχεις, χωρίς να εγκατασταθεί τίποτα, με:
```bash
./install.sh --check
```
Για τη δημόσια IP το script ρωτά το `api.ipify.org` (με εναλλακτική το `ifconfig.me`). Αν δεν το θέλεις, δώσε την IP μόνος σου: `STUDY_PUBLIC_IP=1.2.3.4 ./install.sh`.

Μπορεί να ξανατρέξει με ασφάλεια:
- `git pull && ./install.sh` κάνει update και κρατά τις ρυθμίσεις.
- `STUDY_RECONFIGURE=1 ./install.sh` ξαναρωτάει όλες τις ρυθμίσεις, με τις τωρινές ως προτεινόμενες (οι κωδικοί ορίζονται ξανά).

Οι παρακάτω ενότητες περιγράφουν τα ίδια βήματα χειροκίνητα.

## Deploy στον server, χειροκίνητα (x86_64, Docker, Nginx Proxy Manager)

### 1. DNS
Δύο A/AAAA records προς τον server, π.χ. `study.example.com` και `ntfy.example.com`.

### 2. Κώδικας
```bash
sudo mkdir -p /opt/study && sudo chown $USER /opt/study
# git clone <repo> /opt/study    ή    rsync -a --exclude .venv --exclude .env ./ server:/opt/study/
cd /opt/study
```

### 3. Κοινό δίκτυο με το NPM
Τα containers δεν ανοίγουν ports στο host. Το NPM τα βρίσκει με το όνομά τους μέσα από ένα κοινό docker network.
```bash
docker network create npm_proxy                      # αν δεν υπάρχει ήδη
docker network connect npm_proxy <όνομα-container-NPM>   # π.χ. nginx-proxy-manager-app-1
```
Αν το NPM έχει ήδη δικό του network, π.χ. `npm_default`, βάλε αυτό στο `PROXY_NETWORK` του `.env`.

### 4. `.env`
```bash
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # -> SECRET_KEY
nano .env      # APP_BASE_URL, NTFY_PUBLIC_URL, SECRET_KEY, PROXY_NETWORK
docker compose build app
docker compose run --rm --no-deps app python -m app.hashpw
# αντέγραψε τη γραμμή ADMIN_PASSWORD_HASH='...' στο .env (με τα μονά εισαγωγικά!)
chmod 600 .env
```
> Τα μονά εισαγωγικά χρειάζονται, γιατί το bcrypt hash περιέχει `$`. Χωρίς αυτά το docker compose θα τα ερμήνευε ως μεταβλητές.

### 5. Εκκίνηση
```bash
docker compose up -d --build
docker compose ps          # app και ntfy: (healthy)
```

### 6. Χρήστες και token στο ntfy
```bash
# χρήστης της εφαρμογής: μόνο εγγραφή
docker compose exec ntfy ntfy user add studyapp              # ζητά κωδικό (δεν θα τον ξαναχρειαστείς)
docker compose exec ntfy ntfy access studyapp 'study*' write-only
docker compose exec ntfy ntfy token add studyapp             # -> tk_...  (βάλ' το στο NTFY_TOKEN ή στο dashboard)

# χρήστης του κινητού: μόνο ανάγνωση
docker compose exec ntfy ntfy user add phone                 # κωδικός για το κινητό
docker compose exec ntfy ntfy access phone 'study*' read-only
```
Αν έβαλες το token στο `.env`, τρέξε `docker compose up -d` για να το φορτώσει. Αν το έβαλες από το dashboard (Ρυθμίσεις → ntfy), ισχύει αμέσως.

Ο ntfy έχει `auth-default-access: deny-all`. Κανείς ανώνυμος δεν μπορεί να διαβάσει ή να γράψει. Το topic είναι `study` (ή ό,τι ορίσεις) και πρέπει να ταιριάζει με το `study*`.

### 7. Nginx Proxy Manager: δύο Proxy Hosts

| Πεδίο | Εφαρμογή | ntfy |
|---|---|---|
| Domain Names | `study.example.com` | `ntfy.example.com` |
| Scheme / Forward Hostname / Port | `http` / `study-app` / `8000` | `http` / `study-ntfy` / `80` |
| Block Common Exploits | ✅ | ✅ |
| Websockets Support | — | ✅ **(απαραίτητο)** |
| SSL | Request new Let's Encrypt, Force SSL, HTTP/2, HSTS | ίδιο |

**Advanced → Custom Nginx Configuration**

Εφαρμογή (για uploads έως 50 MB):
```nginx
client_max_body_size 60m;
proxy_read_timeout 120s;
```
ntfy:
```nginx
proxy_buffering off;
proxy_request_buffering off;
proxy_read_timeout 3m;
proxy_send_timeout 3m;
client_max_body_size 1m;
```

### 8. Κινητό (Android)
1. Εγκατάσταση **ntfy** από Google Play ή F-Droid.
2. ⚙️ Ρυθμίσεις → **Manage users** → Add user: server `https://ntfy.example.com`, user `phone` και ο κωδικός του.
3. **＋** → Subscribe to topic: `study`, ✅ *Use another server* → `https://ntfy.example.com`.
4. Προτείνεται: Android ρυθμίσεις → Μπαταρία → εξαίρεση του ntfy από βελτιστοποίηση. Ο self-hosted server στέλνει απευθείας, μέσω μιας μόνιμης σύνδεσης.

### 9. Πρώτα βήματα στο dashboard
1. `https://study.example.com` → login.
2. **Μαθήματα → Νέο μάθημα**: όνομα, ημερομηνία εξέτασης, `AHK_Study_Tracker.xlsx` και `AHK_Diavasma.ics`.
3. **Αρχεία**: ανέβασε τα PDF με τη σωστή κατηγορία. Τα `L01…L13` πάνε στις «Ημερήσιες σημειώσεις» και οι διαφάνειες στις «Διαφάνειες». Οι φωτογραφίες φροντιστηρίων πάνε στα «Φροντιστήρια», με ομάδα π.χ. «Διάλεξη 7 - Ασκήσεις φροντιστηρίου».
   - Τα αρχεία συνδέονται αυτόματα με τις εργασίες βάσει ονόματος. Ένα ⭐ κύριο PDF ανοίγει από την πρωινή ειδοποίηση.
   - Μπορείς να αλλάξεις τη σύνδεση από «✏️ Επεξεργασία εργασίας».
4. **Ρυθμίσεις**: ώρες κ.λπ. → *Στείλε δοκιμαστική ειδοποίηση*.

## Συμπεριφορά ειδοποιήσεων

| Τύπος | Πότε | Συνθήκη |
|---|---|---|
| Πρωινή | ώρα «Πρωινή» | για κάθε εργασία της ημέρας που δεν είναι «Ναι». Αν δεν υπάρχει εργασία αλλά υπάρχουν καθυστερήσεις, στέλνεται μία υπενθύμιση γι' αυτές |
| Βραδινή ×N | κάθε ώρα της λίστας | μόνο αν η εργασία δεν είναι «Ναι» (μια αναβληθείσα εργασία έχει φύγει από τη σημερινή μέρα) |
| Σύνοψη | επιλεγμένη ημέρα/ώρα | πρόοδος 7 ημερών, συνολική, καθυστερήσεις, επόμενες εργασίες |

- **Ημέρες**: οι ειδοποιήσεις στέλνονται μόνο τις επιλεγμένες ημέρες.
- **Ώρες ησυχίας**: ό,τι πέφτει μέσα τους παραλείπεται και φαίνεται στο Ιστορικό.
- **Ανά μάθημα**: οι ειδοποιήσεις απενεργοποιούνται από Μάθημα → Ρυθμίσεις.
- **Παράθυρο ανοχής**: μια ειδοποίηση που η ώρα της πέρασε στέλνεται αν είμαστε ακόμα μέσα στο παράθυρο. Αυτό ισχύει και όταν αλλάξεις ρυθμίσεις ή προσθέσεις εργασία για σήμερα.
- **Αναβολή**: μεταφέρει την εργασία σε άλλη ημέρα (default αύριο) και κρατά την αρχική ημερομηνία. Το υπόλοιπο πλάνο δεν μετακινείται.

## Backup

Τα δεδομένα είναι στα volumes `study_study-data` (βάση, αρχεία, xlsx templates) και `study_ntfy-data` (χρήστες/tokens του ntfy).

```bash
cd /opt/study && mkdir -p backups
# 1) συνεπές αντίγραφο της SQLite ενώ τρέχει η εφαρμογή
docker compose exec app python -m app.backup /data/backups/study-latest.db
# 2) tar όλων των δεδομένων (περιλαμβάνει το παραπάνω αντίγραφο και τα αρχεία)
docker run --rm -v study_study-data:/data:ro -v study_ntfy-data:/ntfy:ro -v "$PWD/backups":/out busybox \
  tar czf /out/study-$(date +%F).tgz -C / data ntfy
# 3) κράτα τα 14 τελευταία
ls -1t backups/study-*.tgz | tail -n +15 | xargs -r rm
```
Καθημερινό cron (`crontab -e`):
```
30 3 * * * cd /opt/study && docker compose exec -T app python -m app.backup /data/backups/study-latest.db && docker run --rm -v study_study-data:/data:ro -v study_ntfy-data:/ntfy:ro -v /opt/study/backups:/out busybox tar czf /out/study-$(date +\%F).tgz -C / data ntfy
```
Αντέγραψε τον φάκελο `backups/` και εκτός server (rsync/restic). Επίσης κράτα ασφαλές αντίγραφο του `.env`. Χωρίς το ίδιο `SECRET_KEY` τα παλιά links και κουμπιά απλώς λήγουν, δεν χάνονται δεδομένα.

**Επαναφορά**:
```bash
docker compose down
docker run --rm -v study_study-data:/data -v study_ntfy-data:/ntfy -v "$PWD/backups":/in busybox \
  sh -c 'rm -rf /data/* /ntfy/* && tar xzf /in/study-YYYY-MM-DD.tgz -C / && cp /data/backups/study-latest.db /data/study.db && rm -f /data/study.db-wal /data/study.db-shm && chown -R 10001:10001 /data && chown -R 10002:10002 /ntfy'
docker compose up -d
```

Εναλλακτικά, για ένα μάθημα: Μάθημα → Ρυθμίσεις → **Εξαγωγή xlsx/CSV**.

## Update
```bash
cd /opt/study
docker compose exec app python -m app.backup      # πάντα πρώτα backup
git pull                                          # ή rsync του νέου κώδικα
docker compose up -d --build
docker image prune -f
```
Για νέα έκδοση του ntfy, άλλαξε το tag `binwiederhier/ntfy:vX.Y.Z` στο `docker-compose.yml` και τρέξε `docker compose pull ntfy && docker compose up -d`. Το σχήμα της βάσης δημιουργείται/επεκτείνεται αυτόματα στην εκκίνηση.

## Τοπική ανάπτυξη και tests
```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt     # Windows: .venv\Scripts\pip
.venv/bin/python -m pytest -q
```
- `tests/test_scheduler.py`: ποια ειδοποίηση στέλνεται πότε (ώρα, ανοχή μετά από restart, ησυχία, ημέρες, αλλαγή ώρας, διπλές, retry).
- `tests/test_import.py`: xlsx/ics, αυτόματη σύνδεση αρχείων, επανεισαγωγή, εξαγωγή που κρατά το γράφημα.
- `tests/test_web.py`: login/CSRF/rate limit, tokens κουμπιών, upload, ρυθμίσεις.

Τοπικά με Docker (ports μόνο στο localhost: app 8000, ntfy 8091):
```bash
docker network create npm_proxy   # μία φορά
# στο .env: APP_BASE_URL=http://localhost:8000, NTFY_PUBLIC_URL=http://localhost:8091, COOKIE_SECURE=false
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build
```

## Ασφάλεια
- **Login ενός χρήστη**: bcrypt hash μόνο από το `.env`, session cookie HttpOnly/Secure/SameSite=Lax, CSRF σε όλες τις φόρμες.
- **Όριο αποτυχιών**: 5 ανά 15′ ανά IP, και 20 συνολικά, που κλειδώνει τον λογαριασμό για 15′ όσες IP κι αν χρησιμοποιηθούν.
- **IP του επισκέπτη**: παίρνεται από την τιμή που προσθέτει ο NPM στο τέλος του `X-Forwarded-For`, οπότε δεν μπορεί να πλαστογραφηθεί από τον client. Αν έχεις και Cloudflare proxy μπροστά από τον NPM, βάλε `TRUSTED_PROXY_HOPS=2` στο `.env`.
- **Logs containers**: έως 3 × 10 MB ανά container.
- **ntfy**: χωρίς web UI (`NTFY_WEB_ROOT=disable`). Το κινητό χρησιμοποιεί μόνο το API.
- Όλα τα secrets (`SECRET_KEY`, hash, `NTFY_TOKEN`) έρχονται από το `.env`. Το token του ntfy μπορεί να οριστεί και από το dashboard, όπου δεν εμφανίζεται ποτέ ξανά.
- Container: μη-root (uid 10001), read-only root filesystem, `cap_drop: ALL`, `no-new-privileges`.
- Headers: CSP χωρίς inline scripts, `X-Frame-Options`, `nosniff`.
- Uploads: έλεγχος κατάληξης **και** περιεχομένου (magic bytes), όριο μεγέθους, αποθήκευση με όνομα από το sha256.

## Αντιμετώπιση προβλημάτων
- **Τα κουμπιά δεν κάνουν τίποτα**: το `APP_BASE_URL` πρέπει να είναι το δημόσιο https URL και να ανοίγει από το κινητό.
- **413 στο upload**: λείπει το `client_max_body_size` στο NPM.
- **Δεν μένεις συνδεδεμένος**: σε σκέτο http χρειάζεται `COOKIE_SECURE=false`, μόνο για τοπική χρήση.
- **«ntfy HTTP 403»** στο Ιστορικό: λάθος token, ή το topic δεν ταιριάζει με το `study*` του `ntfy access`.
- Logs: `docker compose logs -f app` / `docker compose logs -f ntfy`.
