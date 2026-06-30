"""Set SQLite into WAL mode with sane concurrency PRAGMAs on each new connection.

WAL lets the PWA's reads run alongside the till's writes; a till is a single process,
so this is exactly SQLite's sweet spot (spec §7.1). No-op on PostgreSQL (the hub).
"""
from django.db.backends.signals import connection_created
from django.dispatch import receiver


@receiver(connection_created)
def configure_sqlite(sender, connection, **kwargs):
    if connection.vendor != "sqlite":
        return
    cursor = connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA synchronous=NORMAL;")
    cursor.execute("PRAGMA busy_timeout=20000;")
    cursor.execute("PRAGMA foreign_keys=ON;")
