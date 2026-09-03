"""Send ESC/POS bytes to a printer (spec §6/§13.5).

Backend is chosen by the TEYSSIR_PRINTER env so the same code runs in dev and on a till:
    dummy            -> capture only (default; tests, no hardware)
    file:/path/out   -> append bytes to a file (debug, or a CUPS raw queue path)
    tcp:host:port    -> stream to a network ESC/POS printer
A USB backend is a thin addition (pyusb / a raw device path) for the real hardware bridge.
"""
import logging
import os
import socket

logger = logging.getLogger("teyssir.printing")

_last = bytearray()  # captured by the dummy backend (inspectable in tests)


def last_dummy_output():
    return bytes(_last)


def _target_summary(target: str) -> str:
    """Log-safe printer identity (host:port for tcp, no secrets)."""
    if target.startswith("tcp:"):
        return target[len("tcp:"):]
    if target.startswith("file:"):
        return f"file:{target[len('file:'):]}"
    return target


def send(data: bytes, target=None):
    target = target or os.environ.get("TEYSSIR_PRINTER", "dummy")
    nbytes = len(data or b"")
    summary = _target_summary(target)
    try:
        if target == "dummy":
            _last.clear()
            _last.extend(data)
            logger.info("printer ok target=%s bytes=%s", summary, nbytes)
            return nbytes
        if target.startswith("file:"):
            with open(target[len("file:"):], "ab") as fh:
                written = fh.write(data)
            logger.info("printer ok target=%s bytes=%s", summary, written)
            return written
        if target.startswith("tcp:"):
            host, port = target[len("tcp:"):].split(":")
            with socket.create_connection((host, int(port)), timeout=5) as sock:
                sock.sendall(data)
            logger.info("printer ok target=%s bytes=%s", summary, nbytes)
            return nbytes
        raise ValueError(f"Unknown TEYSSIR_PRINTER target: {target!r}")
    except Exception:
        logger.exception("printer failed target=%s bytes=%s", summary, nbytes)
        raise
