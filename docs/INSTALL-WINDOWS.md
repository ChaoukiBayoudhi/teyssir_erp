<p align="center">
  <img src="../assets/branding/logo.png" alt="Teyssir" width="440">
</p>

<h1 align="center">Teyssir — Guide d'installation sur Windows</h1>

<p align="center"><i>Librairie · Point de vente · Gestion — installation pas à pas</i></p>

---

Ce guide explique, **étape par étape**, comment installer Teyssir sur les PC Windows du magasin.
Aucune connaissance technique avancée n'est requise : il suffit de suivre les étapes dans l'ordre.

> **Temps estimé :** ~20 min pour le PC serveur (Hub) + ~10 min par caisse.

---

## 1. Comment Teyssir est organisé

Teyssir fonctionne **même sans Internet**. Chaque caisse garde ses ventes localement et se
synchronise avec le **PC Hub** (le serveur central du magasin) dès qu'il est disponible.

```
                 ┌─────────────────────────┐
                 │        PC HUB           │   ← serveur central (PC-1)
                 │  « Teyssir Hub »        │     rapports, sauvegarde, sync
                 └───────────▲─────────────┘
                             │  réseau local (LAN / Wi-Fi du magasin)
        ┌────────────────────┼────────────────────┐
   ┌────┴────┐          ┌────┴────┐          ┌────┴────┐
   │ Caisse 1│          │ Caisse 2│          │ Caisse 3│   ← tills (C1, C2, C3)
   │   C1    │          │   C2    │          │   C3    │
   └─────────┘          └─────────┘          └─────────┘
```

- **1 PC Hub** (obligatoire) — ne sert **pas** de caisse ; il consolide et sauvegarde.
- **1 à 3 caisses** (tills) — l'application de vente. Chaque caisse a un **code unique** : `C1`, `C2`, `C3`.
- Tous les PC ouvrent Teyssir **dans le navigateur** (Chrome / Edge) à l'adresse `http://localhost:8000`.

---

## 2. Ce qu'il faut avant de commencer

| Sur… | À installer | Où |
|------|-------------|----|
| **PC Hub** (recommandé) | PowerShell **en administrateur** | clic droit → Exécuter en tant qu'administrateur |
| **Chaque PC** | **Python 3.12+** *(le script l'installe via winget s'il manque)* | <https://www.python.org/downloads/windows/> — cocher **« Add python.exe to PATH »** |
| **Un seul PC** (pour préparer le paquet) | **Node.js 20+** *(seulement si `frontend\dist` n'est pas déjà présent ; le script tente winget)* | <https://nodejs.org/> |
| Chaque PC | Un navigateur récent : **Google Chrome** ou **Microsoft Edge** | déjà présent sur Windows |
| Le magasin | Un **réseau local** (routeur/switch) reliant tous les PC | — |

> 💡 **Astuce :** préparez l'application **une seule fois** (avec Node) sur un PC, puis copiez le
> dossier obtenu — dossier `frontend\dist` inclus — sur les autres PC. Ceux-ci n'auront **pas**
> besoin de Node. Python est obligatoire (installé automatiquement si `winget` est disponible).

---

## 3. Récupérer Teyssir

**Option A — avec Git** (si Git est installé) :
```powershell
git clone https://github.com/ChaoukiBayoudhi/teyssir_erp.git
cd teyssir_erp
```

**Option B — sans Git :** téléchargez le ZIP depuis GitHub (bouton **Code ▸ Download ZIP**),
puis décompressez-le, par exemple dans `C:\Teyssir`.

Dans la suite, **« le dossier du projet »** désigne ce dossier (ex. `C:\Teyssir\teyssir_erp`).

---

## 4. Installer le **PC HUB** (serveur central)

1. Ouvrez **PowerShell en administrateur** dans le dossier du projet :
   *Clic droit sur le dossier ▸ « Ouvrir dans le Terminal »*, ou lancez `PowerShell` puis
   `cd C:\Teyssir\teyssir_erp`. Pour PostgreSQL et le pare-feu, **Exécuter en tant qu'administrateur**.

2. Autorisez le script pour cette session, puis lancez l'installation
   (**commande préférée** — journal sous `%LOCALAPPDATA%\Teyssir\logs`, deps winget, puis install complète) :
   ```powershell
   Set-ExecutionPolicy -Scope Process Bypass -Force
   .\deploy\windows\install_all.ps1 -Role hub
   ```
   **Couche application seule** (après deps, ou Python déjà présent) — git pull/clone si besoin, LLM si manquant, puis `install.ps1` + validation :
   ```powershell
   .\deploy\windows\setup_app.ps1 -Role hub
   ```
   Équivalent historique : `.\deploy\windows\install.ps1 -Role hub`.
   Les scripts sont **idempotents** : vous pouvez les relancer sans casser l'installation.
   Voir aussi [`deploy/windows/README.md`](../deploy/windows/README.md) (`setup_app.ps1`).

3. Ce que le script fait **tout seul** :
   * détecte Python 3.11+ (ou l'installe via winget) ;
   * crée `.venv` et installe `requirements.txt` ;
   * construit l'appli web si `frontend\dist` manque et que Node est là ;
   * écrit un `.env` **sans mot de passe en dur** (secrets aléatoires) ;
   * installe **PostgreSQL** si besoin, crée l'utilisateur/base `teyssir` (UTF-8) ;
     en cas d'échec → **SQLite** (`teyssir_hub.sqlite3`) et Teyssir démarre quand même ;
   * `migrate` + `seed_rbac` + `seed_fiscal` ;
   * installe **Ollama** et télécharge le modèle texte `mistral` (optionnel ; l'ERP continue sans) ;
   * ouvre le port **8000** au pare-feu Windows si possible.

4. Le script affiche une **CLÉ DE SYNCHRONISATION** (SYNC KEY), par ex. :
   ```
   SHARED SYNC KEY = 8fK3d9...aZ2
   ^ Use this SAME key on the hub and on every till.
   ```
   ✏️ **Notez cette clé** : vous en aurez besoin pour chaque caisse.

5. Créez le **compte administrateur** quand c'est demandé (identifiant + mot de passe du gérant).
   Si un admin existe déjà, cette étape est **sautée**. Pour une install sans invite :
   ```powershell
   .\deploy\windows\install.ps1 -Role hub -AdminUser owner -AdminPassword "UnMotDePasseFort"
   ```

6. Le backend est enregistré comme **service Windows** `TeyssirBackend` (démarrage automatique, sans fenêtre).
   Un raccourci **Teyssir ERP** est posé sur le Bureau.
   Ouvrez-le : le navigateur par défaut charge **<http://localhost:8000>**.
   Contrôle : **<http://localhost:8000/health/>** doit répondre `ok`.

> Le Hub est prêt. Notez le **nom du PC Hub** (voir §6) — les caisses en auront besoin.

---

## 5. Installer chaque **CAISSE** (till)

Sur **chaque** PC de caisse, dans le dossier du projet :

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
.\deploy\windows\install_all.ps1 -Role till -Terminal C1 -HubUrl http://teyssir-hub.local:8000 -SyncKey COLLER-LA-CLE-DU-HUB
# Ou couche app seule (même paramètres) :
.\deploy\windows\setup_app.ps1 -Role till -Terminal C1 -HubUrl http://teyssir-hub.local:8000 -SyncKey COLLER-LA-CLE-DU-HUB
```

- **`-Terminal`** : `C1` pour la 1ʳᵉ caisse, `C2` pour la 2ᵉ, `C3` pour la 3ᵉ — **jamais deux fois le même**.
- **`-HubUrl`** : l'adresse du Hub (voir §6). Utilisez le **nom** (`teyssir-hub.local`) ou l'**IP** (ex. `http://192.168.1.10:8000`).
- **`-SyncKey`** : **exactement** la clé affichée par le Hub à l'étape 4.3.
- **`-Printer tcp:IP:9100`** (optionnel) : imprimante ticket sur le LAN de **cette** caisse — voir §7.
- **`-DiscoverPrinter`** (optionnel) : scan du /24 sur le port 9100 (ou `.\deploy\windows\Discover-Printer.ps1`). Pas d'IP magasin en dur.

Créez un compte utilisateur (caissier) quand c'est demandé (sauté si un admin existe déjà).
Le raccourci **Teyssir ERP** est créé sur le Bureau ; le service `TeyssirBackend` démarre tout seul.

> Répétez pour C2 et C3. **PostgreSQL n'est jamais installé sur une caisse** — uniquement SQLite (mode hors-ligne).
> Si vous avez oublié `-SyncKey`, relancez la **même** commande avec la clé du Hub : le script met à jour `.env`.

---

## 6. Réseau : rendre le Hub visible depuis les caisses

Les caisses doivent joindre le Hub. Deux méthodes :

**a) Par nom (recommandé)** — `teyssir-hub.local` fonctionne souvent tel quel (mDNS/Bonjour).
Sinon, ajoutez le nom sur **chaque caisse** dans le fichier
`C:\Windows\System32\drivers\etc\hosts` (à éditer en administrateur) :
```
192.168.1.10   teyssir-hub.local
```
(remplacez par l'IP réelle du Hub).

**b) Par IP** — trouvez l'IP du Hub : sur le Hub, ouvrez `cmd` et tapez `ipconfig` → notez
l'« Adresse IPv4 » (ex. `192.168.1.10`). Utilisez alors `-HubUrl http://192.168.1.10:8000`.

**Pare-feu Windows (sur le Hub)** — `install.ps1 -Role hub` essaie d'ouvrir le port **8000** tout seul.
Si la règle manque (PowerShell non administrateur), lancez :
```powershell
New-NetFirewallRule -DisplayName "Teyssir 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
```

✅ **Test :** depuis une caisse, ouvrez `http://teyssir-hub.local:8000/health/` dans le
navigateur — vous devez voir une réponse « ok ».

---

## 7. Imprimante ticket thermique (réseau local du magasin)

L'imprimante ESC/POS se configure avec **`TEYSSIR_PRINTER=tcp:IP:9100`**.
L'IP dépend du **réseau du magasin** (pas celle du PC développeur). Ne laissez pas une
ancienne IP après un déménagement ou un changement de routeur.

**À l'installation** (recommandé) :
```powershell
.\deploy\windows\install.ps1 -Role till -Terminal C1 `
  -HubUrl http://teyssir-hub.local:8000 -SyncKey <clé> `
  -Printer tcp:192.168.1.100:9100
```
Ou scan automatique du /24 (port 9100) — si rien n'est trouvé → `dummy` + avertissement :
```powershell
.\deploy\windows\install.ps1 -Role till -Terminal C1 `
  -HubUrl http://teyssir-hub.local:8000 -SyncKey <clé> -DiscoverPrinter
.\deploy\windows\Discover-Printer.ps1
```

**Après coup** : éditez `.env` (`TEYSSIR_PRINTER=tcp:NOUVELLE-IP:9100`), puis
relancez le service pour recharger l'environnement NSSM :
```powershell
.\deploy\windows\Install-WindowsService.ps1
# ou : Restart-Service TeyssirBackend  (si AppEnvironmentExtra est déjà à jour)
```

**Vérifier :** Menu → **Diagnostics** affiche la cible configurée et un test TCP
(joignable / injoignable). Placeholder dans les exemples : `192.168.1.100` (pas une IP réelle du magasin).

---

## 8. Auto-start & Desktop Shortcut

Après `install.ps1` (PowerShell **administrateur**) :

* Le backend tourne comme service Windows **`TeyssirBackend`** (NSSM + waitress) :
  * démarrage **automatique différé** au boot (sans terminal) ;
  * **redémarrage automatique** en cas de plantage ;
  * journaux dans `logs\teyssir-backend-stdout.log` et `logs\teyssir-backend-stderr.log`.
* Un raccourci Bureau **« Teyssir ERP »** (icône Teyssir) ouvre le navigateur par défaut sur
  `http://localhost:8000` dès que `/health/` répond.
* Les caisses enregistrent aussi la tâche **Teyssir Sync** (toutes les 5 min). Les ventes restent
  locales d'abord ; la sync ne fait que réconcilier avec le Hub.

Vérifier le service :
```powershell
Get-Service TeyssirBackend
sc.exe qc TeyssirBackend
```

Repli si le service n'a pas pu s'installer : `deploy\windows\start-teyssir.bat` (fenêtre à laisser ouverte).
Ne lancez **pas** le `.bat` en même temps que le service — le port **8000** ne peut servir qu'une fois.

Désinstaller service + raccourcis (sans supprimer les données) :
```powershell
.\deploy\windows\uninstall.ps1
```

La tâche planifiée « Teyssir Server » (ancienne méthode, à l'ouverture de session) n'est **pas**
créée si le service existe — pas de double serveur.

Options : `-SkipService`, `-SkipShortcut`. Repli manuel :
```powershell
.\deploy\windows\Install-WindowsService.ps1
.\deploy\windows\Install-DesktopShortcut.ps1
.\deploy\windows\register-autostart.ps1 -Role till -SyncMinutes 5
```

---

## 9. Utilisation quotidienne

1. Allumez le **Hub** en premier, puis les caisses (le service démarre tout seul).
2. Double-cliquez **Teyssir ERP** sur le Bureau (ou le menu Démarrer).
3. Connectez-vous dans le navigateur. L'appli peut être **installée** comme PWA
   (Chrome/Edge : icône « Installer » dans la barre d'adresse).

---

## 10. Sauvegardes (important)

- **Hub PostgreSQL** (cas normal) : sauvegardez la base `teyssir` **et** le dossier `media\` :
  ```
  pg_dump -U teyssir -h 127.0.0.1 teyssir > teyssir-hub.sql
  ```
  (mot de passe = `POSTGRES_PASSWORD` dans `.env`). Copiez aussi `media\` (images des livres).
- **Hub SQLite** (repli) : copiez `teyssir_hub.sqlite3` + `media\`.
- **Caisses** : `teyssir_C1.sqlite3`… (les ventes sont aussi consolidées sur le Hub après sync).

Le Hub contient la **consolidation** de toutes les caisses ; sauvegarder le Hub (+ `media\`)
suffit pour l'essentiel des données de gestion.

---

## 11. Options avancées (facultatif)

<details>
<summary><b>Flags de install.ps1</b></summary>

| Flag | Effet |
|------|--------|
| `-Role hub` / `-Role till` | Type de PC |
| `-Terminal C1` | Code caisse (till) |
| `-HubUrl http://…:8000` | Adresse du Hub (till) |
| `-SyncKey …` | Clé partagée (till ; met à jour `.env` si relancé) |
| `-SkipPostgres` | Hub en SQLite, pas d'install PostgreSQL |
| `-PostgresSuperPassword` / `POSTGRES_ADMIN_PASSWORD` | Mot de passe du superuser Postgres déjà installé |
| `-SkipLlm` / `-LlmModel llama3` | Ollama |
| `-AdminUser` / `-AdminPassword` | Admin sans invite |
| `-SkipAdmin` | Ne pas créer d'utilisateur |
| `-SkipBuild` | Ne pas lancer `npm` |
| `-RegisterAutostart` | Force aussi la tâche « Teyssir Server » (inutile si le service est OK) |
| `-SkipFirewall` | Ne pas ouvrir le port 8000 |
| `-SkipService` | Ne pas installer le service Windows |
| `-SkipShortcut` | Ne pas créer le raccourci Bureau |

</details>

<details>
<summary><b>AI Setup (Automatic) — Ollama local</b></summary>

L'installateur Windows (`install.ps1`) **essaie** d'installer **Ollama** en silence, de démarrer
le service (`http://localhost:11434`) et de télécharger le modèle texte (**mistral**) **et**
le modèle vision bookscan (**`qwen2.5vl:3b`**, Phase 15.7).

- Aucun cloud : tout tourne sur le PC Hub / caisse (hors-ligne après le pull).
- Si Ollama ou un modèle échoue, **Teyssir s'installe quand même** (caisse, stock, livres).
- Options : `-LlmModel llama3`, `-VisionModel qwen2.5vl:3b`, `-SkipVision` (pas de ~2 Go vision),
  `-SkipLlm` (pas d'Ollama).
- Première analyse Vision (cold start CPU) : souvent **20–90 s** — gardez
  `TEYSSIR_SCAN_EXECUTOR=thread`. GPU / Metal accélère ensuite.
- Vérification : `ollama --version`, `ollama list`,
  `.\.venv\Scripts\python.exe manage.py check_llm --ping`.
- Guide : [LOCAL-AI.md](LOCAL-AI.md).

</details>

<details>
<summary><b>Impression des tickets / factures A4</b></summary>

Teyssir génère les factures A4 (PDF) et les tickets. Pour une imprimante thermique ESC/POS,
installez le pilote Windows fourni par le fabricant ; l'impression se fait depuis le navigateur
(Ctrl+P) sur l'imprimante par défaut.
</details>

<details>
<summary><b>Lecture automatique des livres par photo (OCR)</b></summary>

Deux moteurs **gratuits** sont disponibles :

- **Tesseract** (rapide, hors-ligne) : `install.ps1` tente d'installer Tesseract (winget UB-Mannheim)
  avec **ara + fra + eng** et écrit `TEYSSIR_TESSERACT_CMD` dans `.env`. Sinon installez
  manuellement (<https://github.com/UB-Mannheim/tesseract/wiki>), puis dans `.env` :
  `TEYSSIR_OCR_PROVIDER=tesseract` et
  `TEYSSIR_TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe`.
- **Vision-LLM** (extraction structurée multilingue, hors-ligne) : Ollama est installé
  automatiquement **si possible**, avec le modèle **texte** `mistral` **et** le modèle
  **vision** `qwen2.5vl:3b` (CPU-friendly, Phase 15.7). Pour omettre le téléchargement vision
  (~2 Go) : `install.ps1 -SkipVision` ou
  `.\deploy\windows\Install-LocalLlm.ps1 -Model mistral -SkipVision`.
  Gardez `TEYSSIR_OCR_PROVIDER=tesseract` (Vision = couche 2 / fallback). Option primaire :
  `TEYSSIR_OCR_PROVIDER=vision` + `TEYSSIR_SCAN_EXECUTOR=thread`. Variable :
  `TEYSSIR_VISION_MODEL`. Voir `docs/LOCAL-AI.md`.
- **Caméra bas de gamme (ex. XTRIKE ME XPC01)** : flou / bruit attendus — le fallback Vision
  (front+verso) complète Tess ; toujours relire le brouillon (ISBN / prix).
- **ISBN / code-barres** : `pyzbar` (dans `requirements.txt`) a besoin de **libzbar**.
  Sur Windows, placez `libzbar-64.dll` sur le `PATH` du service (ou à côté de Python),
  ou comptez sur la détection client `BarcodeDetector` + OCR chiffres. Sans DLL, le
  décodage barcode serveur échoue silencieusement (fallback OCR digits).

- **Vision fallback (2E / 15.4)** : avec `TEYSSIR_OCR_PROVIDER=tesseract`, Ollama Vision
  (dual-image front+back, `qwen2.5vl:3b`) ne tourne que si le titre/barcode Tess est faible
  (calligraphie arabe, photo téléphone / webcam XTRIKE sans code-barres, titre « garbage »).
  Description 2–4 phrases auto-remplie. ISBN Vision refusé sans checksum ; jamais de
  `barcode_*` inventé. Cold start CPU lent → `TEYSSIR_SCAN_EXECUTOR=thread`.
  Voir `docs/LOCAL-AI.md`.
- **Régression books_photos (2F)** : placez les photos dans `books_photos\`, puis :

```powershell
$env:TEYSSIR_OCR_PROVIDER = "tesseract"
$env:TEYSSIR_OCR_VISION_FALLBACK = "false"
python manage.py bookscan_regression --json
```

  Fixtures : `fixtures\bookscan\expected\*.json`. Détails : `docs/BOOK-OCR-ARCHITECTURE.md` (Phase 2F).

Sans configuration, la saisie du livre reste **manuelle** (aucune erreur, juste pas d'auto-remplissage).

### OCR Troubleshooting

| Symptôme | Cause probable | Action |
|----------|----------------|--------|
| OCR vide sous le service Windows | PATH minimal (NSSM) sans Tesseract | Vérifiez `TEYSSIR_TESSERACT_CMD` dans `.env` + `AppEnvironmentExtra` du service ; Menu → **Diagnostics** |
| Langues manquantes (arabe/français) | Packs non installés | Réinstallez UB Mannheim en cochant **ara**, **fra**, **eng** ; `/health/` → `tesseract.langs` |
| ISBN vide alors que le verso a un code-barres | libzbar absent / photo trop large | Cadrez le barcode en gros plan ; vérifiez `libzbar` / client BarcodeDetector ; Menu → Diagnostics |
| « Image floue, veuillez reprendre » | Flou / faible contraste avant OCR | Reprenez la photo avec plus de lumière, cadrez le titre ; ou « Analyser quand même » |
| Caméra ne s'ouvre pas | HTTP hors localhost | Utilisez `http://localhost:8000` ou HTTPS ; autorisez la caméra dans le navigateur |
| Service ne voit pas Tesseract après install | Redémarrage requis | `nssm restart TeyssirBackend` puis rouvrez Diagnostics |

Contrôle rapide : `http://localhost:8000/health/` doit montrer `"tesseract": {"installed": true, ...}`.
Les admins/owners ont **Menu → Diagnostics** (caméra, OCR, imprimante, DB, LLM).
</details>

<details>
<summary><b>PDF → Word (rapide, non-bloquant)</b></summary>

Sur le Hub Windows la conversion tourne **en arrière-plan** par défaut
(`TEYSSIR_CONVERT_EXECUTOR=thread`) pour ne pas geler la caisse. Les petits PDF texte
passent en mode **Rapide** (PyMuPDF → Word) ; les PDF mixtes utilisent pdf2docx optimisé.
Optionnel dans `.env` : `TEYSSIR_CONVERT_EXECUTOR=inline` (tests) ou `thread`.

### Notes de performance (IMPORTANT)

* L’UI n’est **plus bloquée** : file d’attente → traitement → téléchargement.
* Les **gros PDF** tournent en worker thread ; la caisse / API restent disponibles.
* Stockez `media\` sur un **SSD** local (pas un partage réseau lent).
* **Antivirus** : excluez du scan temps réel Windows Defender :
  * `media\tmp`
  * `media\convert`
  sinon chaque écriture temporaire peut ajouter des secondes.
* Guide complet : [PDF-CONVERSION.md](PDF-CONVERSION.md).
</details>

<details>
<summary><b>PostgreSQL Setup (Automatic) — Hub</b></summary>

Sur le **PC Hub**, `install.ps1 -Role hub` installe PostgreSQL si besoin, crée l'utilisateur et
la base **teyssir** (UTF-8), et écrit le mot de passe dans `.env` (`POSTGRES_*`).

- Les **caisses** restent en **SQLite** (hors-ligne) — PostgreSQL n'y est pas installé.
- Si l'install PostgreSQL échoue, le Hub **bascule sur SQLite** et Teyssir démarre quand même.
- Superutilisateur déjà installé :  
  `$env:POSTGRES_ADMIN_PASSWORD = "…" ; .\deploy\windows\install.ps1 -Role hub`
- Option : `-SkipPostgres` pour forcer SQLite sur le Hub.
- Guide : [POSTGRESQL-SETUP.md](POSTGRESQL-SETUP.md).

</details>

<details>
<summary><b>Hub sur PostgreSQL (magasin à fort volume)</b></summary>

Installez PostgreSQL, créez une base, puis dans le `.env` du Hub : `TEYSSIR_DB=postgres` et
renseignez `POSTGRES_DB / POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_HOST / POSTGRES_PORT`.
Relancez `deploy\windows\start-teyssir.bat` (il applique les migrations).
Détail manuel : [POSTGRESQL-SETUP.md](POSTGRESQL-SETUP.md).
</details>

<details>
<summary><b>Plusieurs magasins (hub cloud)</b></summary>

Donnez à chaque magasin un `TEYSSIR_STORE_CODE` (S1, S2…). Sur chaque Hub, renseignez
`TEYSSIR_CLOUD_HUB_URL` + `TEYSSIR_CLOUD_SYNC_KEY`, planifiez `deploy\windows\sync-to-cloud.bat`,
et consultez le tableau **« Multi-magasins »** pour la consolidation.
</details>

---

## 12. Dépannage

| Problème | Solution |
|----------|----------|
| `python` introuvable | Réinstallez Python 3.12 en cochant **« Add to PATH »**, **fermez** PowerShell, relancez. Désactivez l'alias Microsoft Store (Paramètres → Applications → Alias d'exécution). |
| Script PowerShell bloqué | Lancez d'abord `Set-ExecutionPolicy -Scope Process Bypass -Force`. |
| PostgreSQL / mot de passe superuser | Si Postgres était déjà installé : `$env:POSTGRES_ADMIN_PASSWORD="…" ; .\deploy\windows\install.ps1 -Role hub`. Relancer le script est sûr (la base `teyssir` existante est réutilisée). |
| Hub en SQLite alors que vous vouliez Postgres | Lisez l'avertissement `[PG]` dans la console. Guide : [POSTGRESQL-SETUP.md](POSTGRESQL-SETUP.md). Option `-SkipPostgres` pour rester en SQLite volontairement. |
| La caisse n'atteint pas le Hub | Vérifiez `http://teyssir-hub.local:8000/health/`, le pare-feu (§6), l'IP/nom, et que le Hub tourne. Relancez l'install Hub **en administrateur**. |
| « Bad Request (400) » | Ajoutez le nom/IP du PC dans `TEYSSIR_ALLOWED_HOSTS` du `.env`, relancez. |
| `frontend\dist` manquant | Buildez l'appli sur un PC avec Node (`cd frontend & npm ci & npm run build`) et copiez `frontend\dist`. |
| Port 8000 déjà utilisé | Arrêtez l'autre Teyssir (`nssm stop TeyssirBackend` ou fermez `start-teyssir.bat`). Ou changez `PORT` dans le service / le `.bat`. |
| La clé de sync ne correspond pas | La caisse et le Hub doivent avoir **exactement** la même `TEYSSIR_SYNC_KEY`. Relancez la caisse avec `-SyncKey`. |
| Relancer `install.ps1` | Normal et **sûr** : venv réutilisé, `.env` conservé, `migrate` idempotent, admin non recréé. |

---

## 13. Désinstaller

```powershell
.\deploy\windows\uninstall.ps1
```
Cela arrête et **supprime** le service `TeyssirBackend`, le raccourci Bureau, et les tâches
planifiées. **Sauvegardez d'abord** la base Hub (`pg_dump` ou `teyssir_hub.sqlite3`) et `media\`.
Ensuite vous pouvez supprimer le dossier du projet.

Rapport de validation : [INSTALLATION-QA.md](INSTALLATION-QA.md).

---

<p align="center"><sub>Teyssir — logiciel de gestion de librairie · 100&nbsp;% outils libres et gratuits.</sub></p>
