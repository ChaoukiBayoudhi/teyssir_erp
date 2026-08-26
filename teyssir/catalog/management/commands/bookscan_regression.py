"""Run books_photos A–D book-scan regression (Phase 2F).

Examples (repo root)::

    python manage.py bookscan_regression
    python manage.py bookscan_regression --json
    python manage.py bookscan_regression --vision   # optional Ollama Vision
    python manage.py bookscan_regression --honesty-only

Win11 (PowerShell, Hub)::

    cd C:\\teyssir_erp
    .\\.venv\\Scripts\\Activate.ps1
    $env:TEYSSIR_OCR_PROVIDER = "tesseract"
    $env:TEYSSIR_OCR_VISION_FALLBACK = "false"
    python manage.py bookscan_regression --json
"""
from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from teyssir.catalog.bookscan.regression import (
    books_photos_dir,
    fixtures_dir,
    run_all_fixtures,
)


class Command(BaseCommand):
    help = "Phase 2F: scan books_photos against fixtures/bookscan/expected (offline)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--photos",
            type=Path,
            default=None,
            help="Override books_photos directory",
        )
        parser.add_argument(
            "--fixtures",
            type=Path,
            default=None,
            help="Override fixtures/bookscan/expected directory",
        )
        parser.add_argument(
            "--only",
            nargs="+",
            default=None,
            help="Fixture ids to run (e.g. A_beauty B_premier)",
        )
        parser.add_argument(
            "--vision",
            action="store_true",
            help="Allow Vision fallback (default: skip / offline Tess only)",
        )
        parser.add_argument(
            "--honesty-only",
            action="store_true",
            help="Only assert confidence/ISBN honesty (ignore title/price field misses)",
        )
        parser.add_argument("--json", action="store_true", help="Emit JSON summary")
        parser.add_argument(
            "--fail-skipped",
            action="store_true",
            help="Exit non-zero when a fixture photo is missing",
        )

    def handle(self, *args, **opts):
        photos = opts["photos"] or books_photos_dir()
        fixtures = opts["fixtures"] or fixtures_dir()
        if not fixtures.is_dir():
            raise CommandError(f"No fixtures at {fixtures}")
        if not photos.is_dir():
            raise CommandError(
                f"No photos at {photos}. Copy books_photos/ into the repo root."
            )

        only = set(opts["only"] or []) or None
        results = run_all_fixtures(
            photos_dir=photos,
            fixtures=fixtures,
            vision=bool(opts["vision"]),
            strict_fields=not bool(opts["honesty_only"]),
            only=only,
        )
        if not results:
            raise CommandError(f"No fixture JSON files in {fixtures}")

        summary = {
            "vision": bool(opts["vision"]),
            "honesty_only": bool(opts["honesty_only"]),
            "photos": str(photos),
            "fixtures": str(fixtures),
            "results": [r.as_dict() for r in results],
            "passed": sum(1 for r in results if r.ok and not r.skipped),
            "failed": sum(1 for r in results if not r.ok and not r.skipped),
            "skipped": sum(1 for r in results if r.skipped),
            "total_ms": round(sum(r.ms for r in results), 1),
        }

        if opts["json"]:
            self.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            for r in results:
                status = "SKIP" if r.skipped else ("PASS" if r.ok else "FAIL")
                style = (
                    self.style.WARNING if r.skipped
                    else (self.style.SUCCESS if r.ok else self.style.ERROR)
                )
                self.stdout.write(
                    style(f"[{status}] {r.fixture_id}  {r.ms:.0f} ms  {r.label}")
                )
                if r.skipped:
                    self.stdout.write(f"  {r.skip_reason}")
                    continue
                for c in r.checks:
                    if not c.ok:
                        self.stdout.write(f"  ✗ {c.code}: {c.detail}")
                d = r.draft
                self.stdout.write(
                    f"  title={d.get('title')!r} isbn={d.get('isbn13')!r} "
                    f"barcode={d.get('barcode_raw')!r} price={d.get('price')!r} "
                    f"conf={d.get('confidence')}"
                )
            self.stdout.write(
                f"\nSummary: {summary['passed']} pass / {summary['failed']} fail / "
                f"{summary['skipped']} skip  total={summary['total_ms']:.0f} ms"
            )

        if summary["failed"]:
            raise SystemExit(1)
        if opts["fail_skipped"] and summary["skipped"]:
            raise SystemExit(2)
