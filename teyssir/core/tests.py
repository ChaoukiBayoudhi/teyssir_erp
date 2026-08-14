from decimal import Decimal
import io
import zipfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from teyssir.core import money
from teyssir.core.models import ConvertJob
from teyssir.core.pdfconvert import choose_mode, convert_pdf_to_docx, profile_pdf


class MoneyTests(SimpleTestCase):
    def test_store_scale_is_millime(self):
        self.assertEqual(money.to_money("0.850"), Decimal("0.850"))
        # half-up at the 3rd decimal
        self.assertEqual(money.to_money("1.2505"), Decimal("1.251"))

    def test_display_two_dp_half_up(self):
        self.assertEqual(money.display(Decimal("1.250")), "1.25")
        self.assertEqual(money.display(Decimal("1.255")), "1.26")

    def test_no_float_artifacts(self):
        # 0.1 + 0.2 == 0.30000000000000004 as a float; must still be exactly 0.300
        self.assertEqual(money.to_money(0.1 + 0.2), Decimal("0.300"))

    def test_tva_rates(self):
        self.assertEqual(money.line_tax("10.000", 7), Decimal("0.700"))
        self.assertEqual(money.line_tax("10.000", 13), Decimal("1.300"))
        self.assertEqual(money.line_tax("10.000", 19), Decimal("1.900"))
        self.assertEqual(money.line_tax("10.000", 0), Decimal("0.000"))
        # millime HALF_UP at 19% on 2.550 (the receipt-drift case)
        self.assertEqual(money.line_tax("2.550", 19), Decimal("0.485"))

    def test_millime_integer_roundtrip(self):
        self.assertEqual(money.to_millimes("0.850"), 850)
        self.assertEqual(money.from_millimes(850), Decimal("0.850"))
        self.assertEqual(money.add_money("0.850", "1.250"), Decimal("2.100"))
        self.assertEqual(money.sub_money("2.550", "0.255"), Decimal("2.295"))
        self.assertTrue(money.is_allowed_vat_rate("7.00"))
        self.assertTrue(money.is_allowed_vat_rate(13))
        self.assertFalse(money.is_allowed_vat_rate(20))

    def test_require_non_negative_rejects_negatives(self):
        self.assertEqual(money.require_non_negative_money("12.500"), Decimal("12.500"))
        with self.assertRaises(ValueError):
            money.require_non_negative_money("-1.000", label="sale_price")


class DatabaseConfigTests(SimpleTestCase):
    def test_hub_defaults_to_postgres(self):
        from teyssir.core.db import database_config
        cfg = database_config(role="hub", backend="postgres", base_dir="/tmp",
                              environ={"POSTGRES_PASSWORD": "s3cret", "POSTGRES_DB": "teyssir"})
        self.assertEqual(cfg["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(cfg["NAME"], "teyssir")
        self.assertEqual(cfg["USER"], "teyssir")
        self.assertEqual(cfg["PASSWORD"], "s3cret")
        self.assertEqual(cfg["OPTIONS"]["client_encoding"], "UTF8")
        self.assertGreaterEqual(cfg["CONN_MAX_AGE"], 0)
        self.assertTrue(cfg["CONN_HEALTH_CHECKS"])

    def test_till_stays_on_sqlite(self):
        from pathlib import Path
        from teyssir.core.db import database_config
        cfg = database_config(role="till", backend="sqlite", base_dir="/tmp", terminal="C2")
        self.assertEqual(cfg["ENGINE"], "django.db.backends.sqlite3")
        self.assertEqual(Path(cfg["NAME"]).name, "teyssir_C2.sqlite3")


class QtyTests(SimpleTestCase):
    def test_integer_qty_roundtrip(self):
        from teyssir.core import qty
        self.assertEqual(qty.to_qty("3"), 3)
        self.assertEqual(qty.to_qty(3), 3)
        self.assertEqual(qty.to_qty("3.000"), 3)
        self.assertEqual(qty.format_qty("12.000"), "12")
        self.assertEqual(qty.format_qty(-1), "-1")

    def test_rejects_fractional_qty(self):
        from teyssir.core import qty
        with self.assertRaises(qty.QtyError):
            qty.to_qty("1.5")
        with self.assertRaises(qty.QtyError):
            qty.to_qty(2.3)
        with self.assertRaises(qty.QtyError):
            qty.to_qty("-1")


def _pdf_bytes(text="Hello Teyssir", pages=1):
    import fitz

    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 100), f"{text} p{i + 1}", fontsize=24)
        for line in range(30):
            page.insert_text((72, 140 + line * 14), f"Ligne {line} facture TVA 7%", fontsize=11)
    return doc.tobytes()


class PdfConvertEngineTests(TestCase):
    def test_text_dense_picks_fast_mode(self):
        pdf = _pdf_bytes(pages=2)
        profile = profile_pdf(pdf)
        self.assertTrue(profile.is_text_dense)
        self.assertEqual(choose_mode(profile, "auto"), "fast")

    def test_fast_path_preserves_text(self):
        pdf = _pdf_bytes("Bonjour Teyssir")
        docx, used, _profile = convert_pdf_to_docx(pdf, mode="fast")
        self.assertEqual(used, "fast")
        self.assertTrue(docx.startswith(b"PK"))
        with zipfile.ZipFile(io.BytesIO(docx)) as z:
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
        self.assertIn("Bonjour Teyssir", xml)

    def test_layout_path_produces_docx(self):
        pdf = _pdf_bytes("Layout Mode")
        docx, used, _profile = convert_pdf_to_docx(pdf, mode="layout")
        self.assertEqual(used, "layout")
        self.assertTrue(docx.startswith(b"PK"))
        with zipfile.ZipFile(io.BytesIO(docx)) as z:
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
        self.assertIn("Layout Mode", xml)

    def test_auto_uses_fast_for_text_pdf(self):
        pdf = _pdf_bytes()
        docx, used, _ = convert_pdf_to_docx(pdf, mode="auto")
        self.assertEqual(used, "fast")
        self.assertTrue(docx.startswith(b"PK"))


@override_settings(CONVERT_EXECUTOR="inline")
class PdfToDocxTests(TestCase):
    """PDF -> Word conversion endpoint (sync tiny + async job poll/download)."""

    def _client(self):
        client = APIClient()
        client.force_authenticate(get_user_model().objects.create_superuser(
            "owner", password="pw-strong-123"))
        return client

    def test_converts_pdf_and_docx_contains_the_text(self):
        up = SimpleUploadedFile("facture.pdf", _pdf_bytes(), content_type="application/pdf")
        r = self._client().post("/api/v1/tools/pdf-to-docx", {"file": up}, format="multipart")
        self.assertEqual(r.status_code, 200)
        self.assertIn("wordprocessingml", r["Content-Type"])
        self.assertIn('filename="facture.docx"', r["Content-Disposition"])
        body = b"".join(r.streaming_content) if getattr(r, "streaming_content", None) else r.content
        self.assertTrue(body.startswith(b"PK"))
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
        self.assertIn("Hello Teyssir", xml)

    def test_force_async_returns_202_then_download(self):
        up = SimpleUploadedFile("big.pdf", _pdf_bytes(pages=2), content_type="application/pdf")
        client = self._client()
        r = client.post(
            "/api/v1/tools/pdf-to-docx",
            {"file": up, "async": "1", "mode": "fast"},
            format="multipart",
        )
        self.assertEqual(r.status_code, 202)
        job_id = r.json()["job_id"]
        status = client.get(f"/api/v1/tools/pdf-to-docx/{job_id}")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["status"], "done")
        self.assertIn("download_url", status.json())
        dl = client.get(f"/api/v1/tools/pdf-to-docx/{job_id}/download")
        self.assertEqual(dl.status_code, 200)
        body = b"".join(dl.streaming_content) if getattr(dl, "streaming_content", None) else dl.content
        self.assertTrue(body.startswith(b"PK"))
        self.assertEqual(ConvertJob.objects.get(pk=job_id).status, ConvertJob.DONE)

    def test_rejects_non_pdf(self):
        up = SimpleUploadedFile("notes.txt", b"just text", content_type="text/plain")
        r = self._client().post("/api/v1/tools/pdf-to-docx", {"file": up}, format="multipart")
        self.assertEqual(r.status_code, 400)

    def test_requires_a_file(self):
        r = self._client().post("/api/v1/tools/pdf-to-docx", {}, format="multipart")
        self.assertEqual(r.status_code, 400)


class LocalLlmTests(SimpleTestCase):
    def test_disabled_generate_is_empty(self):
        from teyssir.core.llm import generate, status
        self.assertFalse(status()["enabled"])
        self.assertEqual(generate("hello"), "")

    @override_settings(USE_LLM=True, LLM_PROVIDER="ollama", LLM_MODEL="mistral",
                       OLLAMA_URL="http://127.0.0.1:9")
    def test_enabled_but_down_returns_empty(self):
        from teyssir.core.llm import generate, ollama_reachable, status
        self.assertTrue(status()["enabled"])
        self.assertFalse(ollama_reachable(timeout=0.2))
        self.assertEqual(generate("ping", timeout=1), "")


class HealthLlmTests(TestCase):
    def test_health_reports_llm_config_without_failing(self):
        r = self.client.get("/health/")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "ok")
        self.assertIn("llm", body)
        self.assertIn("enabled", body["llm"])
        self.assertIn("model", body["llm"])
