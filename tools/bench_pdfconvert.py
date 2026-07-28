#!/usr/bin/env python
"""Benchmark PDF → Word engines (before/after style comparison).

Usage (from repo root, venv active):

    python tools/bench_pdfconvert.py

Reports wall-clock seconds for:
  - legacy-like layout (pdf2docx defaults: clip=4.0, stream tables on, no stream=)
  - tuned layout (clip=2.0, no stream tables, stream=)
  - fast text path (PyMuPDF → python-docx)
  - auto (production choose_mode)
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "teyssir.settings")

import django  # noqa: E402

django.setup()

import fitz  # noqa: E402
from pdf2docx import Converter  # noqa: E402

from teyssir.core.pdfconvert import convert_pdf_to_docx  # noqa: E402


def make_text_pdf(pages: int) -> bytes:
    doc = fitz.open()
    for p in range(pages):
        page = doc.new_page()
        y = 50
        for i in range(40):
            page.insert_text(
                (50, y),
                f"Page {p + 1} ligne {i} — Facture Teyssir TVA 7% 19.500 DT",
                fontsize=11,
            )
            y += 14
    return doc.tobytes()


def make_mixed_pdf(pages: int) -> bytes:
    doc = fitz.open()
    for p in range(pages):
        page = doc.new_page()
        y = 50
        for i in range(30):
            page.insert_text((50, y), f"Page {p + 1} mixed line {i}", fontsize=11)
            y += 14
        for r in range(4):
            for c in range(3):
                rect = fitz.Rect(50 + c * 140, 420 + r * 28, 180 + c * 140, 444 + r * 28)
                page.draw_rect(rect)
                page.insert_text((rect.x0 + 4, rect.y0 + 14), f"C{c}R{r}", fontsize=9)
    return doc.tobytes()


def legacy_layout(pdf: bytes) -> float:
    """Simulate the pre-fix path: tempfile PDF + default pdf2docx kwargs."""
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "in.pdf")
        dst = os.path.join(tmp, "out.docx")
        with open(src, "wb") as fh:
            fh.write(pdf)
        t0 = time.perf_counter()
        c = Converter(src)
        try:
            c.convert(dst)  # defaults: clip=4.0, stream tables on
        finally:
            c.close()
        return time.perf_counter() - t0


def tuned_layout(pdf: bytes) -> float:
    t0 = time.perf_counter()
    convert_pdf_to_docx(pdf, mode="layout")
    return time.perf_counter() - t0


def fast_path(pdf: bytes) -> float:
    t0 = time.perf_counter()
    convert_pdf_to_docx(pdf, mode="fast")
    return time.perf_counter() - t0


def auto_path(pdf: bytes) -> float:
    t0 = time.perf_counter()
    convert_pdf_to_docx(pdf, mode="auto")
    return time.perf_counter() - t0


def main():
    cases = [
        ("2p text", make_text_pdf(2)),
        ("10p mixed", make_mixed_pdf(10)),
        ("50p text", make_text_pdf(50)),
    ]
    print(f"{'case':<12} {'legacy':>8} {'tuned':>8} {'fast':>8} {'auto':>8} {'gain':>8}")
    for name, pdf in cases:
        # Warm-up import paths once
        if name.startswith("2p"):
            convert_pdf_to_docx(pdf, mode="fast")
        leg = legacy_layout(pdf)
        tun = tuned_layout(pdf)
        fst = fast_path(pdf)
        aut = auto_path(pdf)
        best = min(tun, fst, aut)
        gain = leg / best if best > 0 else 0
        print(f"{name:<12} {leg:7.2f}s {tun:7.2f}s {fst:7.2f}s {aut:7.2f}s {gain:7.1f}x")


if __name__ == "__main__":
    main()
