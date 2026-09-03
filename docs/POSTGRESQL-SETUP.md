# PostgreSQL setup — Teyssir Hub

The **Hub** uses PostgreSQL in production. **Tills** always use SQLite (offline POS).
The Windows installer tries to install PostgreSQL and create the `teyssir` database
automatically. **If that fails, the hub still installs on SQLite.**

## Automatic (Windows Hub)

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
.\deploy\windows\install_all.ps1 -Role hub
# or focused re-run: .\deploy\windows\install.ps1 -Role hub
```

What it does:

1. Detects `psql`, or silently installs PostgreSQL (winget / EDB; script finds **16–18+** under
   `C:\Program Files\PostgreSQL\<major>\bin`).
2. Starts the Windows service.
3. **If** login as `teyssir` already works, skips create (safe re-run).
4. Otherwise creates role `teyssir` and database `teyssir` (UTF-8).
5. Writes credentials to `.env` (never hardcoded in the repo).
6. Runs `migrate`, `seed_rbac`, `seed_fiscal`. If Django cannot open PostgreSQL, **falls back to SQLite** and continues.

Flags:

| Flag | Effect |
|------|--------|
| `-SkipPostgres` | Do not install PostgreSQL; hub uses SQLite |
| `-PostgresSuperPassword <pwd>` | Existing Postgres superuser password (`postgres`) |
| env `POSTGRES_ADMIN_PASSWORD` | Same as `-PostgresSuperPassword` |

`Install-Postgres.ps1` uses **`-DatabaseName`** (alias `-Database`) for the app DB name.
Do **not** pass `-Db` — on Windows PowerShell 5.1 it conflicts with common parameter `-Debug`
and aborts Postgres setup. Prefer **`install_all.ps1 -Role hub`** so host deps + migrate
`LASTEXITCODE` handling from the feature tip are used.

Re-run with the superuser password if PostgreSQL was already installed:

```powershell
$env:POSTGRES_ADMIN_PASSWORD = "your-postgres-password"
.\deploy\windows\install_all.ps1 -Role hub
```

## Manual setup (any OS)

```bash
createuser -P teyssir          # choose a strong password
createdb -O teyssir -E UTF8 teyssir
```

`.env` on the hub:

```
TEYSSIR_ROLE=hub
TEYSSIR_DB=postgres
POSTGRES_DB=teyssir
POSTGRES_USER=teyssir
POSTGRES_PASSWORD=<the password>
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
```

```bash
pip install "psycopg[binary]>=3.2"   # already in requirements.txt
python manage.py migrate --noinput
python manage.py seed_rbac
python manage.py seed_fiscal
```

macOS: `brew install postgresql@16` then `brew services start postgresql@16`.

## Tills (do not install PostgreSQL)

```
TEYSSIR_ROLE=till
TEYSSIR_DB=sqlite
```

`install.ps1 -Role till` never installs PostgreSQL.

## Troubleshooting

### Port 5432 already in use

Another Postgres (or Docker) owns the port. Either:

- Stop the other instance, or
- Install on another port and set `POSTGRES_PORT` in `.env`, or
- Use `-SkipPostgres` and SQLite until you free 5432.

Windows: `Get-NetTCPConnection -LocalPort 5432`

### Authentication failed (`password authentication failed for user "postgres"`)

The installer needs the **superuser** password only to create the `teyssir` role.

- Pass `-PostgresSuperPassword` / `POSTGRES_ADMIN_PASSWORD`.
- On a fresh silent install, the script sets the superuser password itself.
- Reset (Windows, `psql` as a local admin if `pg_hba.conf` allows):  
  `ALTER USER postgres PASSWORD 'new';`

App connections use `POSTGRES_USER=teyssir` from `.env`, not the superuser.

### `psql` not in PATH after install

Typical location: `C:\Program Files\PostgreSQL\<16|17|18>\bin\psql.exe`
(installer picks the **highest** installed major).  
Open a **new** elevated PowerShell, or add that folder to PATH.

### Django cannot connect

Check `.env`: `TEYSSIR_DB=postgres` and the five `POSTGRES_*` keys.  
`python manage.py check` and `python -c "import django; django.setup(); from django.db import connection; connection.ensure_connection(); print(connection.vendor)"`

If it still fails, set `TEYSSIR_DB=sqlite` and restart — the POS stays up.

### Encoding / Arabic text

The database is created with `ENCODING 'UTF8'`. Django OPTIONS also set `client_encoding=UTF8`.
If you created the DB by hand, use `ENCODING 'UTF8' TEMPLATE template0`.
