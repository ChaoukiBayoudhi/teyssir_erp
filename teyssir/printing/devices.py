"""Send ESC/POS bytes to a printer (spec §6/§13.5).

Backend is chosen by the TEYSSIR_PRINTER env so the same code runs in dev and on a till:
    dummy            -> capture only (default; tests, no hardware)
    file:/path/out   -> append bytes to a file (debug, or a CUPS raw queue path)
    tcp:host:port    -> stream to a network ESC/POS printer
A USB backend is a thin addition (pyusb / a raw device path) for the real hardware bridge.
"""
import os
import socket

_last = bytearray()  # captured by the dummy backend (inspectable in tests)


def last_dummy_output():
    return bytes(_last)


def send(data: bytes, target=None):
    target = target or os.environ.get("TEYSSIR_PRINTER", "dummy")
    if target == "dummy":
        _last.clear()
        _last.extend(data)
        return len(data)
    if target.startswith("file:"):
        with open(target[len("file:"):], "ab") as fh:
            return fh.write(data)
    if target.startswith("tcp:"):
        host, port = target[len("tcp:"):].split(":")
        with socket.create_connection((host, int(port)), timeout=5) as sock:
            sock.sendall(data)
            return len(data)
    raise ValueError(f"Unknown TEYSSIR_PRINTER target: {target!r}")
