"""PDF -> Word (.docx) conversion (free/OSS: pdf2docx + PyMuPDF).

Layout-preserving: text, tables and images survive the conversion. Runs through temp files
because pdf2docx works on paths; everything is cleaned up afterwards. Produces .docx (the modern
Word format — Word/LibreOffice open it and can re-save as legacy .doc if ever needed)."""
import logging
import os
import tempfile

MAX_PDF_BYTES = 25 * 1024 * 1024        # 25 MB guard — keeps a scanned tome from eating the till

# pdf2docx is extremely chatty at INFO level; keep the service log readable.
logging.getLogger("pdf2docx").setLevel(logging.WARNING)


def convert_pdf_to_docx(pdf_bytes):
    """Return .docx bytes for the given PDF bytes. Raises ValueError on non-PDF/oversized input."""
    if not pdf_bytes:
        raise ValueError("empty file")
    if len(pdf_bytes) > MAX_PDF_BYTES:
        raise ValueError("PDF larger than 25 MB")
    if not pdf_bytes.startswith(b"%PDF"):
        raise ValueError("not a PDF file")

    from pdf2docx import Converter

    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "in.pdf")
        dst = os.path.join(tmp, "out.docx")
        with open(src, "wb") as fh:
            fh.write(pdf_bytes)
        converter = Converter(src)
        try:
            converter.convert(dst)
        finally:
            converter.close()
        with open(dst, "rb") as fh:
            return fh.read()
