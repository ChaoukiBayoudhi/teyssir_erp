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
(gérant) quand c'est demandé (sauté si un admin existe déjà).

Le backend est enregistré comme **LaunchAgent** `com.teyssir.backend` (démarrage à la connexion,
sans Terminal). Un raccourci **Teyssir ERP.app** est posé sur le Bureau.

Double-cliquez **Teyssir ERP** sur le Bureau → Safari/Chrome ouvre **<http://localhost:8000>**.
Contrôle : **<http://localhost:8000/health/>** doit répondre `ok`.

> Sans interface graphique pour le service : `bash deploy/macos/start-teyssir.sh` (fenêtre à laisser ouverte).

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
- **`--printer tcp:IP:9100`** (optionnel) : imprimante ticket sur le LAN de **cette** caisse — voir §7.
- **`--discover-printer`** (optionnel) : scan du /24 sur le port 9100.

Créez un compte caissier (sauté si un admin existe déjà). Le raccourci **Teyssir ERP** et le
LaunchAgent sont créés automatiquement. Double-cliquez l'icône Bureau pour ouvrir la caisse.

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

## 7. Imprimante ticket thermique (réseau local du magasin)

L'imprimante ESC/POS se configure avec **`TEYSSIR_PRINTER=tcp:IP:9100`**.
L'IP est celle du **réseau du magasin** (différente du Mac développeur). Après un
changement de réseau, mettez à jour la cible.

**À l'installation :**
```bash
bash deploy/macos/install.sh --role till --terminal C1 \
  --hub-url http://teyssir-hub.local:8000 --sync-key <clé> \
  --printer tcp:192.168.1.100:9100
```
Ou découverte du /24 (port 9100) — soft-fail vers `dummy` :
```bash
bash deploy/macos/install.sh --role till --terminal C1 \
  --hub-url http://teyssir-hub.local:8000 --sync-key <clé> --discover-printer
bash deploy/macos/discover-printer.sh
```

**Après coup :** éditez `.env` (`TEYSSIR_PRINTER=tcp:NOUVELLE-IP:9100`), puis
ré-enregistrez le LaunchAgent (il lit `.env` et injecte `TEYSSIR_PRINTER` dans le plist) :
```bash
bash deploy/macos/Install-BackendService.sh
# ou : bash deploy/macos/Install-BackendService.sh --printer tcp:NOUVELLE-IP:9100
```

**Vérifier :** Menu → **Diagnostics** (cible + test TCP). Exemples : `192.168.1.100` (placeholder).

---

## 8. Auto-start & Desktop Shortcut (Mac)

Après `install.sh` :

* Le backend tourne comme **LaunchAgent** `com.teyssir.backend` (équivalent du service Windows) :
  * démarre à **l'ouverture de session** (sans fenêtre Terminal) ;
  * **KeepAlive** — redémarre s'il quitte ;
  * journaux dans `logs/teyssir-backend-stdout.log` et `logs/teyssir-backend-stderr.log`.
* Un app **« Teyssir ERP.app »** sur le Bureau (et `~/Applications`) ouvre le navigateur par défaut
  sur `http://localhost:8000` dès que `/health/` répond. Icône : `assets/branding/teyssir.icns`.

Vérifier :
```bash
launchctl list | grep teyssir
curl -sf http://127.0.0.1:8000/health/
```

Repli manuel (si le LaunchAgent n'a pas pu s'installer) :
```bash
bash deploy/macos/start-teyssir.sh
```
Ne lancez **pas** le script Terminal en même temps que le LaunchAgent — le port **8000** ne peut
servir qu'une fois.

Options : `--skip-service`, `--skip-shortcut`.

Désinstaller agent + raccourcis (sans supprimer les données) :
```bash
bash deploy/macos/uninstall.sh
```

Sur les caisses, `com.teyssir.sync` synchronise avec le Hub toutes les 5 min.

---

## 9. Utilisation quotidienne

1. Allumez le **Hub** d'abord, puis les caisses (le LaunchAgent démarre tout seul à la connexion).
2. Double-cliquez **Teyssir ERP** sur le Bureau.
3. Connectez-vous. Dans Chrome : **⋮ ▸ Installer Teyssir** pour une PWA plein écran dans le Dock.

---

## 10. Sauvegardes

Les données sont des fichiers dans le dossier du projet : `teyssir_hub.sqlite3` (Hub),
`teyssir_C1.sqlite3`… (caisses) et le dossier `media/` (images des livres).
Copiez chaque soir `teyssir_hub.sqlite3` + `media/` du **Hub** sur un disque externe / iCloud /
Time Machine. Le Hub contient déjà la consolidation de toutes les caisses.

---

## 11. Options (facultatif)

<details>
<summary><b>Lecture des livres par photo (OCR) — gratuit</b></summary>

- **Tesseract** (rapide, hors-ligne) : `install.sh` tente `brew install tesseract tesseract-lang`
  et écrit `TEYSSIR_TESSERACT_CMD` (ex. `/opt/homebrew/bin/tesseract`) dans `.env` + LaunchAgent.
  Sinon : `brew install tesseract tesseract-lang`, puis `.env` :
  `TEYSSIR_OCR_PROVIDER=tesseract` et `TEYSSIR_TESSERACT_CMD=/opt/homebrew/bin/tesseract`.
- **Vision-LLM** (extraction structurée multilingue, hors-ligne) : `install.sh` tente
  `brew install ollama` puis **`ollama pull qwen2.5vl:3b`** (Phase 15.7, CPU-friendly).
  Opt-out : `--skip-vision`. Laissez `TEYSSIR_OCR_PROVIDER=tesseract` — Vision est un
  fallback (couvertures arabes / OCR faible / webcam type XTRIKE / sans ISBN).
  `TEYSSIR_VISION_MODEL` est écrit dans `.env`. Pour Vision en primaire :
  `TEYSSIR_OCR_PROVIDER=vision` + `TEYSSIR_SCAN_EXECUTOR=thread`. Cold start CPU possible
  (dizaines de secondes). Voir `docs/LOCAL-AI.md` (Phase 15.4 dual-image).
- Si OCR est vide sous LaunchAgent : vérifiez `PATH` Homebrew dans le plist et
  `TEYSSIR_TESSERACT_CMD` ; Menu → **Diagnostics** ;   `curl -s http://127.0.0.1:8000/health/ | jq .tesseract`.
  Vérifiez que `langs` contient **ara** et **fra** (sinon `brew install tesseract-lang`).
  Les couvertures arabes sans `ara` produisent du Latin absurde (`wis! Boot ay`) et `languages=en`.
</details>

<details>
<summary><b>Hub sur PostgreSQL / plusieurs magasins</b></summary>

`brew install postgresql@16`, créez une base, puis `.env` du Hub : `TEYSSIR_DB=postgres` +
`POSTGRES_*`. Multi-magasins : donnez un `TEYSSIR_STORE_CODE` (S1, S2…) et renseignez
`TEYSSIR_CLOUD_HUB_URL` + `TEYSSIR_CLOUD_SYNC_KEY`, planifiez `deploy/macos/sync-to-cloud.sh`.
</details>

---

## 12. Dépannage

| Problème | Solution |
|----------|----------|
| `python3` introuvable | `brew install python@3.12`, rouvrez le Terminal. |
| `permission denied` sur un script | Lancez-le via `bash deploy/macos/<script>.sh`. |
| La caisse n'atteint pas le Hub | Testez `http://teyssir-hub.local:8000/health/` ; vérifiez IP/nom, pare-feu, et que le Hub tourne. |
| « Bad Request (400) » | Ajoutez le nom/IP du Mac dans `TEYSSIR_ALLOWED_HOSTS` du `.env`, relancez. |
| `frontend/dist` manquant | `cd frontend && npm ci && npm run build` (ou `brew install node`). |
| Port 8000 occupé | Arrêtez l'autre instance : `bash deploy/macos/Install-BackendService.sh --remove` ou fermez le Terminal `start-teyssir.sh`. Ou : `TEYSSIR_PORT=8080`. |
| LaunchAgent ne répond pas | `tail -50 logs/teyssir-backend-stderr.log` puis `bash deploy/macos/Install-BackendService.sh`. |
| OCR vide (LaunchAgent) | `TEYSSIR_TESSERACT_CMD` manquant ou PATH sans Homebrew — réinstallez le service ; Menu → Diagnostics. |
| OCR arabe = Latin / `en` | Pack `ara` manquant : `brew install tesseract-lang`, vérifiez `/health/` → `tesseract.langs`, relancez le LaunchAgent. |
| Clé de sync incorrecte | Le Hub et la caisse doivent avoir **exactement** la même `TEYSSIR_SYNC_KEY`. |

---

## 13. Désinstaller

```bash
bash deploy/macos/uninstall.sh
```
Cela retire le LaunchAgent et les raccourcis Bureau / Applications. **Sauvegardez d'abord**
`teyssir_hub.sqlite3` (ou `pg_dump`) et `media/`, puis supprimez le dossier du projet si besoin.

---

<p align="center"><sub>Teyssir — logiciel de gestion de librairie · 100&nbsp;% outils libres et gratuits · testé sur MacBook Pro M1.</sub></p>
