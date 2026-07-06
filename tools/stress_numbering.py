"""Concurrency stress test for the fiscal-numbering allocator. Run standalone:
    python tools/stress_numbering.py
Spawns N threads allocating document numbers against a temp SQLite DB and asserts they are all
unique, gapless, and error-free. Removing SQLite's transaction_mode=IMMEDIATE reproduces the
pre-fix 'database is locked' failures."""
import os, sys, django, threading, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # project root
os.environ["DJANGO_SETTINGS_MODULE"] = "teyssir.settings"
os.environ.setdefault("TEYSSIR_ROLE", "till")
os.environ.setdefault("TEYSSIR_TERMINAL", "C1")
os.environ.setdefault("DEBUG", "1")
os.environ["TEYSSIR_SQLITE_NAME"] = "conc_test.sqlite3"
django.setup()
from django.core.management import call_command
call_command("migrate", "--noinput", verbosity=0)

from django.utils import timezone
from django.db import connections
from teyssir.billing.services import allocate_document_number
from teyssir.billing.models import DocumentCounter

when = timezone.make_aware(datetime.datetime(2027, 3, 10, 12, 0))
DocumentCounter.objects.filter(terminal="CC", year=2027, month=3, doc_type="FACTURE").delete()

N = 40
seqs, errors, lock, barrier = [], [], threading.Lock(), threading.Barrier(N)

def worker():
    barrier.wait()                      # release all threads at once -> max contention
    try:
        _, seq, _ = allocate_document_number("CC", "FACTURE", when)
        with lock: seqs.append(seq)
    except Exception as e:
        with lock: errors.append(repr(e))
    finally:
        connections.close_all()

ts = [threading.Thread(target=worker) for _ in range(N)]
for t in ts: t.start()
for t in ts: t.join()

dups = sorted(s for s in set(seqs) if seqs.count(s) > 1)
print(f"threads={N}  returned={len(seqs)}  unique={len(set(seqs))}  errors={len(errors)}")
print(f"DUPLICATE seqs (BUG if non-empty): {dups}")
if errors:
    print(f"sample error: {errors[0]}")
final = DocumentCounter.objects.get(terminal="CC", year=2027, month=3, doc_type="FACTURE").seq
print(f"counter.seq={final}  (should equal number of successful allocations)")
