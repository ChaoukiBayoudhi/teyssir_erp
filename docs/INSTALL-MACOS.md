<p align="center">
  <img src="../assets/branding/logo.png" alt="Teyssir" width="440">
</p>

<h1 align="center">Teyssir — Guide d'installation sur macOS (MacBook Pro M1)</h1>

<p align="center"><i>Librairie · Point de vente · Gestion — installation pas à pas</i></p>

---

Ce guide explique comment installer Teyssir sur un **Mac (Apple Silicon M1/M2/M3 ou Intel)**.
Il fonctionne pour un Mac servant de **Hub** (serveur central) et/ou de **caisse**.

> **Temps estimé :** ~20 min pour le Mac Hub + ~10 min par caisse.
> ✅ Testé sur **MacBook Pro M1**.

---

## 1. Organisation

Teyssir fonctionne **même sans Internet**. Chaque caisse enregistre ses ventes localement puis se
synchronise avec le **Hub** (le serveur central) dès qu'il est joignable.

```
                 ┌─────────────────────────┐
                 │   Mac HUB « Teyssir »   │   ← serveur central : rapports, sauvegarde, sync
                 └───────────▲─────────────┘
                             │  réseau local (Wi-Fi / Ethernet du magasin)
        ┌────────────────────┼────────────────────┐
   ┌────┴────┐          ┌────┴────┐          ┌────┴────┐
   │ Caisse 1│          │ Caisse 2│          │ Caisse 3│   ← C1, C2, C3
   └─────────┘          └─────────┘          └─────────┘
```

Tous les postes ouvrent Teyssir **dans Safari ou Chrome** à l'adresse `http://localhost:8000`.

---

## 2. Prérequis

| Sur… | À installer | Comment |
|------|-------------|---------|
| Chaque Mac | **Python 3.12+** | `brew install python@3.12` (ou <https://www.python.org/downloads/macos/>) |
| Un seul Mac *(si l'appli n'est pas déjà « buildée »)* | **Node.js 20+** | `brew install node` |
| Chaque Mac | **Homebrew** (recommandé) | <https://brew.sh> — sur M1 il s'installe dans `/opt/homebrew` |
| Chaque Mac | **Safari** ou **Google Chrome** | déjà présents |

Vérifier Homebrew et Python (Terminal → app **Terminal**) :
```bash
brew --version
python3 --version      # doit afficher 3.12 ou plus
```

> 💡 Préparez l'application **une seule fois** avec Node, puis copiez le dossier (dossier
> `frontend/dist` inclus) sur les autres Mac : ceux-ci n'auront besoin que de Python.

---

## 3. Récupérer Teyssir

**Avec Git :**
```bash
git clone https://github.com/ChaoukiBayoudhi/teyssir_erp.git
cd teyssir_erp
```
**Sans Git :** téléchargez le ZIP sur GitHub (**Code ▸ Download ZIP**), décompressez-le (ex. dans
`~/Teyssir`), puis dans le Terminal : `cd ~/Teyssir/teyssir_erp`.

Dans la suite, **« le dossier du projet »** = ce dossier.

---

## 4. Installer le **Mac HUB**

Dans le Terminal, au dossier du projet :
```bash
bash deploy/macos/install.sh --role hub
```

Le script installe tout, puis affiche une **CLÉ DE SYNCHRONISATION** (SYNC KEY) :
```
SHARED SYNC KEY = 8fK3d9...aZ2
^ Use this SAME key on the hub and on every till.
```
✏️ **Notez cette clé** — chaque caisse en aura besoin. Créez ensuite le **compte administrateur**
(gérant) quand c'est demandé.

Démarrez le serveur :
```bash
bash deploy/macos/start-teyssir.sh
```
Laissez cette fenêtre **ouverte**, puis ouvrez **<http://localhost:8000>** dans le navigateur.

---

## 5. Installer chaque **CAISSE**

Sur chaque Mac de caisse :
```bash
bash deploy/macos/install.sh --role till --terminal C1 \
     --hub-url http://teyssir-hub.local:8000 --sync-key COLLER-LA-CLE-DU-HUB
```
- **`--terminal`** : `C1`, `C2`, `C3` — **unique** par caisse.
- **`--hub-url`** : adresse du Hub (nom `teyssir-hub.local` ou IP, ex. `http://192.168.1.10:8000`).
- **`--sync-key`** : **exactement** la clé du Hub (étape 4).

Créez un compte caissier, puis :
```bash
bash deploy/macos/start-teyssir.sh
```
Ouvrez **<http://localhost:8000>** sur la caisse.

---

## 6. Réseau : joindre le Hub

Sur macOS, les noms **`.local`** fonctionnent en général tels quels (Bonjour). Pour donner au Hub
le nom `teyssir-hub.local` :

1. Sur le Hub : **Réglages Système ▸ Général ▸ Partage ▸ Nom d'hôte local** → mettez `teyssir-hub`.
2. Sinon, utilisez l'**IP** du Hub : `ipconfig getifaddr en0` (Wi-Fi) ou `en1` (Ethernet), puis
   `--hub-url http://<IP>:8000`.

**Pare-feu** (souvent désactivé sur Mac) : si activé, **Réglages Système ▸ Réseau ▸ Pare-feu ▸
Options** → autorisez les connexions entrantes pour `python`/Teyssir.

✅ **Test :** depuis une caisse, ouvrez `http://teyssir-hub.local:8000/health/` → réponse « ok ».

---

## 7. Démarrage automatique + synchronisation

Pour lancer Teyssir à l'ouverture de session et synchroniser régulièrement :
```bash
# sur le Hub :
bash deploy/macos/register-autostart.sh hub
# sur chaque caisse (sync toutes les 300 s) :
bash deploy/macos/register-autostart.sh till 300
```
Cela crée des **LaunchAgents** (`com.teyssir.server`, `com.teyssir.sync`). Vérifier :
`launchctl list | grep teyssir`. Pour tout retirer : `bash deploy/macos/register-autostart.sh --remove`.

---

## 8. Utilisation quotidienne

1. Allumez le **Hub** d'abord, puis les caisses (Teyssir démarre seul si l'auto-start est activé).
2. Ouvrez **<http://localhost:8000>**, connectez-vous.
3. Dans Chrome : **⋮ ▸ Installer Teyssir** pour un lancement plein écran (icône dans le Dock).

---

## 9. Sauvegardes

Les données sont des fichiers dans le dossier du projet : `teyssir_hub.sqlite3` (Hub),
`teyssir_C1.sqlite3`… (caisses) et le dossier `media/` (images des livres).
Copiez chaque soir `teyssir_hub.sqlite3` + `media/` du **Hub** sur un disque externe / iCloud /
Time Machine. Le Hub contient déjà la consolidation de toutes les caisses.

---

## 10. Options (facultatif)

<details>
<summary><b>Lecture des livres par photo (OCR) — gratuit</b></summary>

- **Tesseract** (rapide, hors-ligne) : `brew install tesseract tesseract-lang`, puis dans `.env` :
  `TEYSSIR_OCR_PROVIDER=tesseract`.
- **Vision-LLM** (extraction structurée multilingue, hors-ligne) : `brew install ollama`,
  `ollama pull qwen2.5vl:3b`, puis `.env` : `TEYSSIR_OCR_PROVIDER=vision` et
  `TEYSSIR_SCAN_EXECUTOR=thread`.
</details>

<details>
<summary><b>Hub sur PostgreSQL / plusieurs magasins</b></summary>

`brew install postgresql@16`, créez une base, puis `.env` du Hub : `TEYSSIR_DB=postgres` +
`POSTGRES_*`. Multi-magasins : donnez un `TEYSSIR_STORE_CODE` (S1, S2…) et renseignez
`TEYSSIR_CLOUD_HUB_URL` + `TEYSSIR_CLOUD_SYNC_KEY`, planifiez `deploy/macos/sync-to-cloud.sh`.
</details>

---

## 11. Dépannage

| Problème | Solution |
|----------|----------|
| `python3` introuvable | `brew install python@3.12`, rouvrez le Terminal. |
| `permission denied` sur un script | Lancez-le via `bash deploy/macos/<script>.sh`. |
| La caisse n'atteint pas le Hub | Testez `http://teyssir-hub.local:8000/health/` ; vérifiez IP/nom, pare-feu, et que le Hub tourne. |
| « Bad Request (400) » | Ajoutez le nom/IP du Mac dans `TEYSSIR_ALLOWED_HOSTS` du `.env`, relancez. |
| `frontend/dist` manquant | `cd frontend && npm ci && npm run build` (ou `brew install node`). |
| Port 8000 occupé | Démarrez avec `TEYSSIR_PORT=8080 bash deploy/macos/start-teyssir.sh`. |
| Clé de sync incorrecte | Le Hub et la caisse doivent avoir **exactement** la même `TEYSSIR_SYNC_KEY`. |

---

<p align="center"><sub>Teyssir — logiciel de gestion de librairie · 100&nbsp;% outils libres et gratuits · testé sur MacBook Pro M1.</sub></p>


## Receipt printer (LAN)

Set `TEYSSIR_PRINTER` to `tcp:HOST:9100` (or `dummy` / `file:/path`). Discover helpers:

- Windows: `deploy/windows/Discover-Printer.ps1`
- macOS: `deploy/macos/discover-printer.sh`
- Cross-platform: `python deploy/discover_printer.py`

Installer `-Printer` / `--printer` / discover flags land fully when Windows/macOS UX install slices are merged.
