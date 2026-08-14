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
| **Chaque PC** (Hub + caisses) | **Python 3.12+** — cocher **« Add python.exe to PATH »** | <https://www.python.org/downloads/windows/> |
| **Un seul PC** (pour préparer le paquet) | **Node.js 20+** *(seulement si l'appli n'est pas déjà « buildée »)* | <https://nodejs.org/> |
| Chaque PC | Un navigateur récent : **Google Chrome** ou **Microsoft Edge** | déjà présent sur Windows |
| Le magasin | Un **réseau local** (routeur/switch) reliant tous les PC | — |

> 💡 **Astuce :** préparez l'application **une seule fois** (avec Node) sur un PC, puis copiez le
> dossier obtenu — dossier `frontend\dist` inclus — sur les autres PC. Ceux-ci n'auront **pas**
> besoin de Node, seulement de Python.

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

1. Ouvrez **PowerShell** dans le dossier du projet :
   *Clic droit sur le dossier ▸ « Ouvrir dans le Terminal »*, ou lancez `PowerShell` puis
   `cd C:\Teyssir\teyssir_erp`.

2. Autorisez le script pour cette session, puis lancez l'installation :
   ```powershell
   Set-ExecutionPolicy -Scope Process Bypass -Force
   .\deploy\windows\install.ps1 -Role hub
   ```

3. Le script installe tout, puis affiche une **CLÉ DE SYNCHRONISATION** (SYNC KEY), par ex. :
   ```
   SHARED SYNC KEY = 8fK3d9...aZ2
   ^ Use this SAME key on the hub and on every till.
   ```
   ✏️ **Notez cette clé** : vous en aurez besoin pour chaque caisse.

4. Créez le **compte administrateur** quand c'est demandé (identifiant + mot de passe du gérant).

5. Démarrez le serveur :
   ```
   deploy\windows\start-teyssir.bat
   ```
   Laissez **cette fenêtre ouverte**. Ouvrez ensuite le navigateur sur **<http://localhost:8000>**.

> Le Hub est prêt. Notez le **nom du PC Hub** (voir §6) — les caisses en auront besoin.

---

## 5. Installer chaque **CAISSE** (till)

Sur **chaque** PC de caisse, dans le dossier du projet :

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
.\deploy\windows\install.ps1 -Role till -Terminal C1 -HubUrl http://teyssir-hub.local:8000 -SyncKey COLLER-LA-CLE-DU-HUB
```

- **`-Terminal`** : `C1` pour la 1ʳᵉ caisse, `C2` pour la 2ᵉ, `C3` pour la 3ᵉ — **jamais deux fois le même**.
- **`-HubUrl`** : l'adresse du Hub (voir §6). Utilisez le **nom** (`teyssir-hub.local`) ou l'**IP** (ex. `http://192.168.1.10:8000`).
- **`-SyncKey`** : **exactement** la clé affichée par le Hub à l'étape 4.3.

Créez un compte utilisateur (caissier) quand c'est demandé, puis démarrez :
```
deploy\windows\start-teyssir.bat
```
Ouvrez **<http://localhost:8000>** sur la caisse et connectez-vous.

> Répétez pour C2 et C3.

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

**Pare-feu Windows (sur le Hub)** — autorisez le port **8000** :
```powershell
New-NetFirewallRule -DisplayName "Teyssir 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
```

✅ **Test :** depuis une caisse, ouvrez `http://teyssir-hub.local:8000/health/` dans le
navigateur — vous devez voir une réponse « ok ».

---

## 7. Démarrage automatique + synchronisation planifiée

Pour que Teyssir démarre tout seul et que les caisses se synchronisent régulièrement, lancez
(PowerShell administrateur, dans le dossier du projet) :

- Sur le **Hub** :
  ```powershell
  .\deploy\windows\register-autostart.ps1 -Role hub
  ```
- Sur chaque **caisse** :
  ```powershell
  .\deploy\windows\register-autostart.ps1 -Role till -SyncMinutes 5
  ```

Cela crée des tâches planifiées Windows : **« Teyssir Server »** (démarrage à l'ouverture de
session) et, sur les caisses, **« Teyssir Sync »** (toutes les 5 min). Les ventes sont **toujours**
enregistrées localement d'abord ; la sync ne fait que réconcilier avec le Hub.

---

## 8. Utilisation quotidienne

1. Allumez le **Hub** en premier, puis les caisses.
2. Sur chaque PC, Teyssir démarre tout seul (ou double-cliquez `start-teyssir.bat`).
3. Ouvrez le navigateur sur **<http://localhost:8000>** et connectez-vous.
4. L'appli peut être **installée** comme une application (Chrome/Edge : icône « Installer » dans la
   barre d'adresse) pour un lancement plein écran.

---

## 9. Sauvegardes (important)

Toutes les données du magasin sont dans le dossier du projet, sous forme de fichiers :
`teyssir_hub.sqlite3` (Hub), `teyssir_C1.sqlite3`… (caisses), et le dossier `media\` (images des
livres).

- **Sauvegarde simple :** copiez chaque soir le fichier `teyssir_hub.sqlite3` et le dossier
  `media\` du **Hub** sur une clé USB ou un disque externe.
- Le Hub contient déjà la **consolidation** de toutes les caisses ; sauvegarder le Hub suffit pour
  l'essentiel des données de gestion.

---

## 10. Options avancées (facultatif)

<details>
<summary><b>AI Setup (Automatic) — Ollama local</b></summary>

L'installateur Windows (`install.ps1`) **essaie** d'installer **Ollama** en silence, de démarrer
le service (`http://localhost:11434`) et de télécharger le modèle par défaut (**mistral**).

- Aucun cloud : tout tourne sur le PC Hub / caisse.
- Si Ollama ou le modèle échoue, **Teyssir s'installe quand même** (caisse, stock, livres).
- Options : `-LlmModel llama3` ou `-SkipLlm`.
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

- **Tesseract** (rapide, hors-ligne) : installez Tesseract pour Windows
  (<https://github.com/UB-Mannheim/tesseract/wiki>) avec les langues **ara + fra + eng**, puis dans
  `.env` : `TEYSSIR_OCR_PROVIDER=tesseract`.
- **Vision-LLM** (extraction structurée multilingue, hors-ligne) : **installé automatiquement**
  avec Ollama si possible (voir ci-dessous **AI Setup**). Sinon installez
  [Ollama](https://ollama.com), `ollama pull qwen2.5vl:3b`, puis `.env` :
  `TEYSSIR_OCR_PROVIDER=vision` et `TEYSSIR_SCAN_EXECUTOR=thread`.

Sans configuration, la saisie du livre reste **manuelle** (aucune erreur, juste pas d'auto-remplissage).
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

## 11. Dépannage

| Problème | Solution |
|----------|----------|
| `python` introuvable | Réinstallez Python en cochant **« Add to PATH »**, rouvrez PowerShell. |
| Script PowerShell bloqué | Lancez d'abord `Set-ExecutionPolicy -Scope Process Bypass -Force`. |
| La caisse n'atteint pas le Hub | Vérifiez `http://teyssir-hub.local:8000/health/`, le pare-feu (§6), l'IP/nom, et que le Hub tourne. |
| « Bad Request (400) » | Ajoutez le nom/IP du PC dans `TEYSSIR_ALLOWED_HOSTS` du `.env`, relancez. |
| `frontend\dist` manquant | Buildez l'appli sur un PC avec Node (`cd frontend & npm ci & npm run build`) et copiez `frontend\dist`. |
| Port 8000 déjà utilisé | Éditez `set PORT=8000` dans `start-teyssir.bat` (ex. `8080`). |
| La clé de sync ne correspond pas | La caisse et le Hub doivent avoir **exactement** la même `TEYSSIR_SYNC_KEY`. |

---

## 12. Désinstaller

Arrêtez le serveur (fermez la fenêtre), supprimez les tâches planifiées dans **Task Scheduler**
(« Teyssir Server », « Teyssir Sync »), puis supprimez le dossier du projet. **Sauvegardez d'abord**
`teyssir_hub.sqlite3` et `media\` si vous voulez garder les données.

---

<p align="center"><sub>Teyssir — logiciel de gestion de librairie · 100&nbsp;% outils libres et gratuits.</sub></p>
