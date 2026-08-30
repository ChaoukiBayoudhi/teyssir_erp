<p align="center">
  <img src="../assets/branding/logo.png" alt="Teyssir" width="440">
</p>

<h1 align="center">Teyssir — Guide d'installation sur Windows</h1>

<p align="center"><i>Librairie · Point de vente · Gestion — installation pas à pas (PowerShell uniquement)</i></p>

---

Ce guide explique, **étape par étape**, comment installer Teyssir sur les PC Windows du magasin.
Aucune connaissance technique avancée n'est requise : ouvrez **PowerShell**, copiez les commandes
ci-dessous, et suivez l'ordre.

> **Temps estimé :** ~20 min pour le PC serveur (Hub) + ~10 min par caisse.  
> **Critère d'acceptation magasin :** cochez la grille **Win11 dry-run** dans
> [`INSTALLATION-QA.md`](INSTALLATION-QA.md#win11-dry-run-checklist-phase-7) avant la mise en production.

---

## Chemin rapide (à lire en premier)

| PC | Commande préférée (premier lancement) |
|----|----------------------------------------|
| **Hub** | `.\deploy\windows\install_all.ps1 -Role hub` |
| **Caisse C1** | `.\deploy\windows\setup_caisse_C1.ps1 -HubUrl http://…:8000 -SyncKey … -DiscoverPrinter` |
| **Caisse C2 / C3** | `setup_caisse_C2.ps1` / `setup_caisse_C3.ps1` (mêmes paramètres) |

Toujours commencer par :
```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
```
dans le **dossier du projet** (ex. `C:\Teyssir\teyssir_erp`).

**Ordre magasin :** (1) installer le Hub → (2) noter la **SYNC KEY** → (3) installer chaque caisse → (4) tester `/health/` → (5) cocher la checklist Win11.

Détail des scripts : [`deploy/windows/README.md`](../deploy/windows/README.md).

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
Vous devez y voir `manage.py` et le dossier `deploy\windows`.

---

## 4. Installer le **PC HUB** (serveur central)

### 4.1 Commande préférée — `install_all.ps1`

1. Ouvrez **PowerShell en administrateur** dans le dossier du projet :
   *Clic droit sur le dossier ▸ « Ouvrir dans le Terminal »*, ou lancez `PowerShell` puis
   `cd C:\Teyssir\teyssir_erp`. Pour PostgreSQL et le pare-feu, **Exécuter en tant qu'administrateur**.

2. Autorisez les scripts pour cette session, puis lancez l'installation complète
   (**premier lancement recommandé**) :
   ```powershell
   Set-ExecutionPolicy -Scope Process Bypass -Force
   .\deploy\windows\install_all.ps1 -Role hub
   ```

   **Ce que fait `install_all.ps1` :**
   * journal sous `%LOCALAPPDATA%\Teyssir\logs` ;
   * dépendances hôte via winget (Python, éventuellement Node / Tesseract) ;
   * Ollama + modèles locaux s'ils manquent (voir §8) ;
   * puis l'installateur applicatif (`install.ps1`).

   **Sortie attendue (extrait) :** fin du script avec un message du type
   `==== install_all finished (exit 0) ====` et le chemin du journal.
   Sur le Hub, une ligne **SHARED SYNC KEY = …** apparaît — **notez-la**.

### 4.2 Couche application seule — `setup_app.ps1`

Utilisez cette commande **après** les deps, ou si Python / Node sont déjà installés
(réinstall, mise à jour app, validation) :
```powershell
.\deploy\windows\setup_app.ps1 -Role hub
```

`setup_app.ps1` : git pull/clone si besoin → LLM si manquant → `install.ps1` → contrôles
(`django check`, migrate, `frontend\dist`, `/health/` si le serveur tourne).

Équivalent historique (même spine) : `.\deploy\windows\install.ps1 -Role hub`.  
Les scripts sont **idempotents** : vous pouvez les relancer sans casser l'installation.

### 4.3 Ce que l'installateur Hub fait tout seul

* détecte Python 3.11+ (ou l'installe via winget) ;
* crée `.venv` et installe `requirements.txt` ;
* construit l'appli web si `frontend\dist` manque et que Node est là ;
* écrit un `.env` **sans mot de passe en dur** (secrets aléatoires, UTF-8 sans BOM) ;
* installe **PostgreSQL** si besoin, crée l'utilisateur/base `teyssir` (UTF-8) ;
  en cas d'échec → **SQLite** (`teyssir_hub.sqlite3`) avec un avertissement `[PG]` — Teyssir démarre quand même ;
* `migrate` + `seed_rbac` + `seed_fiscal` ;
* installe **Ollama** et les modèles `mistral` + `qwen2.5vl:3b` (optionnel ; l'ERP continue sans) ;
* ouvre le port **8000** au pare-feu Windows si possible ;
* enregistre le service Windows **`TeyssirBackend`** et le raccourci Bureau **« Teyssir ERP »**.

### 4.4 Clé de synchronisation et compte admin

Le script affiche une **CLÉ DE SYNCHRONISATION** (SYNC KEY), par ex. :
```
SHARED SYNC KEY = 8fK3d9...aZ2
^ Use this SAME key on the hub and on every till.
```
✏️ **Notez cette clé** : vous en aurez besoin pour chaque caisse.

Créez le **compte administrateur** quand c'est demandé (identifiant + mot de passe du gérant).
Si un admin existe déjà, cette étape est **sautée**. Pour une install sans invite :
```powershell
.\deploy\windows\install_all.ps1 -Role hub -AdminUser owner -AdminPassword "UnMotDePasseFort"
```

### 4.5 Vérification Hub

* Double-cliquez **Teyssir ERP** sur le Bureau (pas de fenêtre console noire persistante).
* Le navigateur ouvre **<http://localhost:8000>**.
* Contrôle : **<http://localhost:8000/health/>** doit répondre `ok`.

> Le Hub est prêt. Notez le **nom ou l'IP du PC Hub** (voir §7) — les caisses en auront besoin.

---

## 5. Installer chaque **CAISSE** (till)

Sur **chaque** PC de caisse, dans le dossier du projet.
**PostgreSQL n'est jamais installé sur une caisse** — uniquement SQLite (mode hors-ligne).

### 5.1 Scripts dédiés par caisse (recommandé)

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force

# 1ʳᵉ caisse — Terminal=C1 (idempotent) :
.\deploy\windows\setup_caisse_C1.ps1 `
  -HubUrl http://teyssir-hub.local:8000 `
  -SyncKey COLLER-LA-CLE-DU-HUB `
  -DiscoverPrinter

# 2ᵉ caisse :
.\deploy\windows\setup_caisse_C2.ps1 `
  -HubUrl http://teyssir-hub.local:8000 `
  -SyncKey COLLER-LA-CLE-DU-HUB

# 3ᵉ caisse (exemple avec code magasin) :
.\deploy\windows\setup_caisse_C3.ps1 `
  -HubUrl http://teyssir-hub.local:8000 `
  -SyncKey COLLER-LA-CLE-DU-HUB `
  -StoreCode S1
```

**Chaîne d'appels :**  
`setup_caisse_Cx.ps1` → `setup_caisse.ps1` → `setup_app.ps1` → `install.ps1`  
(pas d'installeur parallèle : une seule spine).

| Paramètre | Rôle |
|-----------|------|
| **`-HubUrl`** | Adresse du Hub (nom ou IP), ex. `http://192.168.1.10:8000` — voir §7 |
| **`-SyncKey`** | **Exactement** la clé affichée sur le Hub (§4.4) |
| **`-DiscoverPrinter`** | Scan du réseau local (/24, port 9100) pour l'imprimante ticket ; si rien → `dummy` + avertissement |
| **`-StoreCode`** | Optionnel — code magasin (`TEYSSIR_STORE_CODE`), ex. `S1` |
| **`-Printer tcp:IP:9100`** | Optionnel — IP connue de l'imprimante (sans scan) |

Contrôles **sans** réinstaller :
```powershell
.\deploy\windows\setup_caisse_C1.ps1 -ValidateOnly -HubUrl http://teyssir-hub.local:8000
# Imprimante seule :
.\deploy\windows\Discover-Printer.ps1
```

### 5.2 Entrées génériques (équivalent)

Si vous préférez `install_all` / `setup_app` avec `-Role till` :
```powershell
.\deploy\windows\install_all.ps1 -Role till -Terminal C1 `
  -HubUrl http://teyssir-hub.local:8000 -SyncKey COLLER-LA-CLE-DU-HUB -DiscoverPrinter

.\deploy\windows\setup_app.ps1 -Role till -Terminal C1 `
  -HubUrl http://teyssir-hub.local:8000 -SyncKey COLLER-LA-CLE-DU-HUB
```

Ou paramétré :  
`.\deploy\windows\setup_caisse.ps1 -Terminal C1 -HubUrl … -SyncKey …`

- **`-Terminal` / wrapper Cx** : `C1`, `C2`, `C3` — **jamais deux fois le même** sur le réseau magasin.
- Variables d'environnement (si paramètre vide) : `TEYSSIR_TERMINAL`, `TEYSSIR_STORE_CODE`,
  `TEYSSIR_HUB_URL`, `TEYSSIR_SYNC_KEY`, `TEYSSIR_PRINTER`.

Créez un compte utilisateur (caissier) quand c'est demandé (sauté si un admin existe déjà).
Le raccourci **Teyssir ERP** est créé sur le Bureau ; le service `TeyssirBackend` démarre tout seul.

> Si vous avez oublié `-SyncKey`, relancez la **même** commande avec la clé du Hub : le script met à jour `.env`.

---

## 6. Raccourci Bureau « Teyssir ERP » (sans console)

Après une install réussie (PowerShell administrateur recommandé) :

* Un raccourci **« Teyssir ERP »** est posé sur le **Bureau** et dans le menu Démarrer
  (icône `assets/branding/teyssir.ico`).
* Il lance `open-teyssir.vbs` → attend `/health/` → ouvre le **navigateur par défaut**
  sur `http://localhost:8000`.
* **Aucune fenêtre console noire** ne reste ouverte (contrairement à `start-teyssir.bat`).

Recréer le raccourci seul :
```powershell
.\deploy\windows\Install-DesktopShortcut.ps1
```

Repli manuel (fenêtre à laisser ouverte — **ne pas** combiner avec le service) :
`deploy\windows\start-teyssir.bat`.

---

## 7. Réseau : rendre le Hub visible depuis les caisses

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

**Pare-feu Windows (sur le Hub)** — l'install Hub essaie d'ouvrir le port **8000** tout seul.
Si la règle manque (PowerShell non administrateur), lancez :
```powershell
New-NetFirewallRule -DisplayName "Teyssir 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
```

✅ **Test :** depuis une caisse, ouvrez `http://teyssir-hub.local:8000/health/` dans le
navigateur — vous devez voir une réponse « ok ».

---

## 8. IA locale (Ollama) — mistral + vision

Par défaut, `install_all.ps1` / `setup_app.ps1` / `install.ps1` **installent Ollama** s'il manque,
démarrent le service (`http://localhost:11434`), et téléchargent :

| Modèle | Usage | Taille indicative |
|--------|--------|-------------------|
| **`mistral`** | Texte / assistance locale | ~4 Go |
| **`qwen2.5vl:3b`** | Vision bookscan (photo de livre) | ~2 Go |

* Tout reste **hors-ligne** après le téléchargement (pas de cloud obligatoire).
* Si le disque / le réseau / Ollama échoue → **soft-fail** : Teyssir (caisse, stock, livres) s'installe quand même.
* Première analyse Vision (CPU froid) : souvent **20–90 s** — normal.

**Opt-out :**
```powershell
# Pas d'Ollama du tout :
.\deploy\windows\install_all.ps1 -Role hub -SkipLlm

# Ollama + mistral, sans le modèle vision (~2 Go) :
.\deploy\windows\install_all.ps1 -Role hub -SkipVision
.\deploy\windows\setup_caisse_C1.ps1 -HubUrl … -SyncKey … -SkipVision
```

**Vérification attendue :**
```powershell
ollama --version
ollama list
# doit lister mistral (et qwen2.5vl:3b sauf -SkipVision)
.\.venv\Scripts\python.exe manage.py check_llm --ping
```

Relancer plus tard : `.\deploy\windows\Install-LocalLlm.ps1`  
Guide détaillé : [LOCAL-AI.md](LOCAL-AI.md).

---

## 9. Auto-démarrage & désenregistrement

Après `install.ps1` / `install_all.ps1` (PowerShell **administrateur**) :

* Le backend tourne comme service Windows **`TeyssirBackend`** (NSSM + waitress) :
  * démarrage **automatique différé** au boot (sans terminal) ;
  * **redémarrage automatique** en cas de plantage ;
  * journaux dans `logs\teyssir-backend-stdout.log` et `logs\teyssir-backend-stderr.log`.
* Les caisses enregistrent aussi la tâche **Teyssir Sync** (toutes les 5 min). Les ventes restent
  locales d'abord ; la sync ne fait que réconcilier avec le Hub.
* **Une seule** écoute sur le port **8000** : pas de double serveur (service + ancienne tâche « Teyssir Server »).

### Options à l'installation

| Flag | Effet |
|------|--------|
| **`-SkipAutostart`** | N'enregistre **pas** les tâches planifiées (sync / repli logon). Le service NSSM s'installe **toujours** sauf `-SkipService`. |
| `-SkipService` | Pas de service Windows |
| `-SkipShortcut` | Pas de raccourci Bureau |
| `-RegisterAutostart` | Force aussi le repli « Teyssir Server » au logon si le service est absent |

Exemple caisse sans tâches planifiées :
```powershell
.\deploy\windows\setup_caisse_C1.ps1 -HubUrl … -SyncKey … -SkipAutostart
```

### Vérifier / désenregistrer

```powershell
Get-Service TeyssirBackend
sc.exe qc TeyssirBackend
Get-ScheduledTask -TaskName "Teyssir Sync","Teyssir Server" -ErrorAction SilentlyContinue
```

| Action | Commande |
|--------|----------|
| Installer le service (boot auto) | `.\deploy\windows\Install-WindowsService.ps1` |
| Sync caisse (5 min) | `.\deploy\windows\register-autostart.ps1 -Role till` |
| **Désenregistrer** sync / tâche logon | `Unregister-ScheduledTask -TaskName "Teyssir Sync","Teyssir Server" -Confirm:$false` |
| Arrêter le service (garde l'install) | `nssm stop TeyssirBackend` puis `nssm set TeyssirBackend Start SERVICE_DEMAND_START` |
| Remettre le démarrage auto | `nssm set TeyssirBackend Start SERVICE_DELAYED_AUTO_START` ; `nssm start TeyssirBackend` |
| Tout retirer (service + tâches + raccourcis, **données conservées**) | `.\deploy\windows\uninstall.ps1` |

---

## 10. Imprimante ticket thermique (réseau local)

L'imprimante ESC/POS se configure avec **`TEYSSIR_PRINTER=tcp:IP:9100`**.
L'IP dépend du **réseau du magasin**. Aucune IP magasin / marque n'est figée dans les scripts.

**À l'installation (recommandé sur C1) :**
```powershell
.\deploy\windows\setup_caisse_C1.ps1 `
  -HubUrl http://teyssir-hub.local:8000 -SyncKey <clé> -DiscoverPrinter
```

Scan manuel :
```powershell
.\deploy\windows\Discover-Printer.ps1
```
**Sortie attendue :** une ligne `tcp:x.x.x.x:9100` si un appareil répond sur 9100, sinon `dummy` + avertissement.

**Après coup :** éditez `.env` (`TEYSSIR_PRINTER=tcp:NOUVELLE-IP:9100`), puis :
```powershell
.\deploy\windows\Install-WindowsService.ps1
# ou : Restart-Service TeyssirBackend
```

**Vérifier :** Menu → **Diagnostics** (cible + test TCP). Placeholder doc : `192.168.1.100` (pas une IP réelle du magasin).

---

## 11. Utilisation quotidienne

1. Allumez le **Hub** en premier, puis les caisses (le service démarre tout seul).
2. Double-cliquez **Teyssir ERP** sur le Bureau (ou le menu Démarrer).
3. Connectez-vous dans le navigateur. L'appli peut être **installée** comme PWA
   (Chrome/Edge : icône « Installer » dans la barre d'adresse).

Après une mise à jour de l'appli, si l'écran semble « ancien » : **rafraîchissement forcé PWA**
(voir dépannage §14 — Ctrl+Shift+R / vider le cache du site).

---

## 12. Sauvegardes (important)

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

## 13. Options avancées (facultatif)

<details>
<summary><b>Flags de install.ps1 / install_all.ps1 / setup_app.ps1</b></summary>

| Flag | Effet |
|------|--------|
| `-Role hub` / `-Role till` | Type de PC |
| `-Terminal C1` | Code caisse (till) |
| `-HubUrl http://…:8000` | Adresse du Hub (till) |
| `-SyncKey …` | Clé partagée (till ; met à jour `.env` si relancé) |
| `-DiscoverPrinter` | Scan imprimante ticket LAN |
| `-SkipPostgres` | Hub en SQLite, pas d'install PostgreSQL |
| `-PostgresSuperPassword` / `POSTGRES_ADMIN_PASSWORD` | Mot de passe du superuser Postgres déjà installé |
| `-SkipLlm` / `-LlmModel llama3` | Ollama (opt-out / autre modèle texte) |
| `-SkipVision` / `-VisionModel qwen2.5vl:3b` | Modèle vision bookscan |
| `-AdminUser` / `-AdminPassword` | Admin sans invite |
| `-SkipAdmin` | Ne pas créer d'utilisateur |
| `-SkipBuild` | Ne pas lancer `npm` |
| `-RegisterAutostart` | Force aussi la tâche « Teyssir Server » si le service est absent |
| `-SkipAutostart` | Ne pas enregistrer les tâches planifiées ; service NSSM reste par défaut |
| `-SkipFirewall` | Ne pas ouvrir le port 8000 |
| `-SkipService` | Ne pas installer le service Windows |
| `-SkipShortcut` | Ne pas créer le raccourci Bureau |

</details>

<details>
<summary><b>OCR / lecture des livres par photo</b></summary>

Deux moteurs **gratuits** sont disponibles :

- **Tesseract** (rapide, hors-ligne) : l'installateur tente winget UB-Mannheim avec
  **ara + fra + eng** et écrit `TEYSSIR_TESSERACT_CMD` dans `.env`. Sinon :
  <https://github.com/UB-Mannheim/tesseract/wiki>, puis
  `TEYSSIR_OCR_PROVIDER=tesseract` et le chemin vers `tesseract.exe`.
- **Vision-LLM** : Ollama + `qwen2.5vl:3b` (voir §8). Gardez
  `TEYSSIR_OCR_PROVIDER=tesseract` (Vision = couche 2 / fallback) sauf besoin primaire.
- **Caméra bas de gamme** : flou / bruit attendus — toujours relire le brouillon (ISBN / prix).
- **ISBN / code-barres** : `pyzbar` a besoin de **libzbar** sur Windows (DLL sur le PATH du service).
  Sans DLL, fallback OCR chiffres / BarcodeDetector navigateur.

Sans configuration, la saisie du livre reste **manuelle**.

Contrôle : `http://localhost:8000/health/` → `"tesseract": {"installed": true, ...}` ;
Menu → **Diagnostics**. Voir aussi [LOCAL-AI.md](LOCAL-AI.md) et [BOOK-OCR-ARCHITECTURE.md](BOOK-OCR-ARCHITECTURE.md).
</details>

<details>
<summary><b>PDF → Word (rapide, non-bloquant)</b></summary>

Sur le Hub Windows la conversion tourne **en arrière-plan** par défaut
(`TEYSSIR_CONVERT_EXECUTOR=thread`). Guide : [PDF-CONVERSION.md](PDF-CONVERSION.md).
Excluez du scan Defender : `media\tmp` et `media\convert`.
</details>

<details>
<summary><b>PostgreSQL Setup (Automatic) — Hub</b></summary>

Sur le **PC Hub**, l'installateur installe PostgreSQL si besoin et crée la base **teyssir**.
Les **caisses** restent en **SQLite**. Si Postgres échoue → **bascule SQLite** + message `[PG]`.
Option `-SkipPostgres` pour forcer SQLite. Guide : [POSTGRESQL-SETUP.md](POSTGRESQL-SETUP.md).
</details>

<details>
<summary><b>Plusieurs magasins (hub cloud)</b></summary>

Donnez à chaque magasin un `TEYSSIR_STORE_CODE` (S1, S2…). Sur chaque Hub :
`TEYSSIR_CLOUD_HUB_URL` + `TEYSSIR_CLOUD_SYNC_KEY`, planifiez `deploy\windows\sync-to-cloud.bat`.
</details>

---

## 14. Dépannage

| Problème | Solution |
|----------|----------|
| **Port 8000 déjà utilisé** / page ne charge pas | Un seul processus doit écouter : `nssm stop TeyssirBackend` **ou** fermez `start-teyssir.bat` — **pas les deux**. Vérifiez : `Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue`. Puis `nssm start TeyssirBackend`. |
| **PostgreSQL → SQLite (soft-fail)** | Message `[PG]` dans la console : le Hub tourne en `teyssir_hub.sqlite3`. Relancez en admin, ou `$env:POSTGRES_ADMIN_PASSWORD="…"` puis `install_all.ps1 -Role hub`. Guide : [POSTGRESQL-SETUP.md](POSTGRESQL-SETUP.md). |
| **Tesseract : langues manquantes** (arabe / français) | Réinstallez UB Mannheim en cochant **ara**, **fra**, **eng**. Contrôle : `/health/` → `tesseract.langs`. |
| **OCR vide** sous le service Windows | PATH minimal NSSM : vérifiez `TEYSSIR_TESSERACT_CMD` dans `.env`, puis `nssm restart TeyssirBackend`. Menu → **Diagnostics**. |
| **Imprimante / Discover** | Relancez `.\deploy\windows\Discover-Printer.ps1` ; l'imprimante doit être sur le **même LAN**, port **9100**. Si rien → `dummy` (normal). Pas d'IP inventée. |
| **PWA / écran « ancien » après maj** | **Hard-refresh** : Chrome/Edge → `Ctrl+Shift+R`. Ou Paramètres site → Effacer les données ; ou désinstaller la PWA puis rouvrir `http://localhost:8000`. |
| `python` introuvable | Réinstallez Python 3.12 avec **« Add to PATH »**, fermez PowerShell, relancez. Désactivez l'alias Microsoft Store. |
| Script PowerShell bloqué | `Set-ExecutionPolicy -Scope Process Bypass -Force`. |
| La caisse n'atteint pas le Hub | Testez `http://…:8000/health/` depuis la caisse, pare-feu §7, IP/nom, Hub allumé / service démarré. |
| « Bad Request (400) » | Ajoutez le nom/IP dans `TEYSSIR_ALLOWED_HOSTS` du `.env`, relancez le service. |
| `frontend\dist` manquant | Sur un PC avec Node : `cd frontend ; npm ci ; npm run build`, puis copiez `frontend\dist`. |
| Clé de sync incorrecte | Même `TEYSSIR_SYNC_KEY` sur Hub et caisse. Relancez la caisse avec `-SyncKey`. |
| Relancer `install_all` / `setup_app` | **Sûr** : venv réutilisé, `.env` conservé, migrate idempotent, admin non recréé. |
| Raccourci ouvre une console | Utilisez le raccourci créé par l'install (via `.vbs`). Recréez : `Install-DesktopShortcut.ps1`. |

### OCR (détail)

| Symptôme | Action |
|----------|--------|
| ISBN vide alors que le verso a un code-barres | Cadrez le barcode ; vérifiez libzbar / BarcodeDetector ; Diagnostics |
| « Image floue, veuillez reprendre » | Plus de lumière, cadrez le titre ; ou « Analyser quand même » |
| Caméra ne s'ouvre pas | Utilisez `http://localhost:8000` (pas une IP externe en HTTP) ; autorisez la caméra |

---

## 15. Désinstaller (données conservées)

```powershell
.\deploy\windows\uninstall.ps1
```
Cela arrête et **supprime** le service `TeyssirBackend`, le raccourci Bureau, et les tâches
planifiées. Le dossier projet, les bases (SQLite/Postgres), `media\` et `.env` **restent**.
**Sauvegardez** avant une suppression manuelle du dossier.

---

## 16. Critère d'acceptation — checklist Win11

Avant la mise en production magasin, **cochez** la grille complète :

👉 **[Win11 dry-run checklist (Phase 7) — INSTALLATION-QA.md](INSTALLATION-QA.md#win11-dry-run-checklist-phase-7)**

Résumé des points à valider sur un vrai PC Windows 11 :

1. Hub : `install_all.ps1 -Role hub` (ou soft-fail Postgres → SQLite documenté)
2. SYNC KEY notée ; Ollama `mistral` + `qwen2.5vl:3b` (sauf `-SkipLlm` / `-SkipVision`)
3. Service `TeyssirBackend` + **un seul** listener sur le port **8000**
4. Raccourci **Teyssir ERP** sans console persistante ; `/health/` + UI POS
5. Caisse : `setup_caisse_C1.ps1 … -DiscoverPrinter`
6. Ticket / Diagnostics imprimante ; reboot → autostart OK (sauf `-SkipAutostart`)
7. `uninstall.ps1` retire service/raccourcis/tâches **sans** effacer les données

Rapport QA plus large : [INSTALLATION-QA.md](INSTALLATION-QA.md).

---

<p align="center"><sub>Teyssir — logiciel de gestion de librairie · 100&nbsp;% outils libres et gratuits.</sub></p>
