"""Minimal ESC/POS command builder (spec §13.5) — no third-party dependency.

Produces the raw byte stream a thermal printer understands. Kept tiny and pure so the
receipt layout is fully unit-testable without any hardware.
"""
ESC = b"\x1b"
GS = b"\x1d"
_ALIGN = {"left": 0, "center": 1, "right": 2}


class Escpos:
    def __init__(self, width=42, codepage="cp1252"):
        self.width = width          # characters per line (80 mm ≈ 42 @ font A)
        self.codepage = codepage
        self.buf = bytearray()
        self.init()

    def init(self):
        self.buf += ESC + b"@"      # initialize printer
        return self

    def align(self, mode):
        self.buf += ESC + b"a" + bytes([_ALIGN[mode]])
        return self

    def bold(self, on=True):
        self.buf += ESC + b"E" + bytes([1 if on else 0])
        return self

    def size(self, w=1, h=1):       # 1..8 multipliers
        n = ((w - 1) << 4) | (h - 1)
        self.buf += GS + b"!" + bytes([n])
        return self

    def text(self, s):
        self.buf += str(s).encode(self.codepage, "replace")
        return self

    def line(self, s=""):
        return self.text(s).feed()

    def rule(self, ch="-"):
        return self.line(ch * self.width)

    def row(self, left, right):
        """Left text + right-aligned text on one line (e.g. label .... amount)."""
        left, right = str(left), str(right)
        gap = max(1, self.width - len(left) - len(right))
        return self.line(left + " " * gap + right)

    def feed(self, n=1):
        self.buf += b"\n" * n
        return self

    def cut(self):
        self.buf += GS + b"V" + b"\x00"   # full cut
        return self

    def kick(self):
        self.buf += ESC + b"p" + bytes([0, 25, 250])  # open cash drawer (pin 2)
        return self

    def bytes(self):
        return bytes(self.buf)
