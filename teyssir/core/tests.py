from decimal import Decimal

from django.test import SimpleTestCase, TestCase

from teyssir.core import money


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
        self.assertEqual(money.line_tax("10.000", 19), Decimal("1.900"))
        self.assertEqual(money.line_tax("10.000", 0), Decimal("0.000"))




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

class PdfToDocxTests(TestCase):
    """PDF -> Word conversion endpoint (free/offline pdf2docx)."""

    @staticmethod
    def _pdf_bytes(text="Hello Teyssir"):
        import fitz  # PyMuPDF (ships with pdf2docx)

        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 100), text, fontsize=24)
        return doc.tobytes()

    def _client(self):
        from django.contrib.auth import get_user_model
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(get_user_model().objects.create_superuser(
            "owner", password="pw-strong-123"))
        return client

    def test_converts_pdf_and_docx_contains_the_text(self):
        import io
        import zipfile

        from django.core.files.uploadedfile import SimpleUploadedFile

        up = SimpleUploadedFile("facture.pdf", self._pdf_bytes(), content_type="application/pdf")
        r = self._client().post("/api/v1/tools/pdf-to-docx", {"file": up}, format="multipart")
        self.assertEqual(r.status_code, 200)
        self.assertIn("wordprocessingml", r["Content-Type"])
        self.assertIn('filename="facture.docx"', r["Content-Disposition"])
        body = b"".join(r.streaming_content) if getattr(r, "streaming_content", None) else r.content
        self.assertTrue(body.startswith(b"PK"))                     # a real .docx is a zip
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
        self.assertIn("Hello Teyssir", xml)                         # text survived the conversion

    def test_rejects_non_pdf(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        up = SimpleUploadedFile("notes.txt", b"just text", content_type="text/plain")
        r = self._client().post("/api/v1/tools/pdf-to-docx", {"file": up}, format="multipart")
        self.assertEqual(r.status_code, 400)

    def test_requires_a_file(self):
        r = self._client().post("/api/v1/tools/pdf-to-docx", {}, format="multipart")
        self.assertEqual(r.status_code, 400)
