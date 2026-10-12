"""Tricky files for upload fields: the ones real users send and testers rarely have at hand.

Made on first use, in pure Python (no image library needed), into the evidence folder. The
agent names them "@file:<name>"; Mirmera puts the file in the field, and its replays can too.
Each file knows what it really is, so a rule like "JPG or PNG, up to 5 MB" can be checked
without AI: a 25 MB photo or an .exe renamed .jpg that is accepted breaks it.
"""
import base64
import os
import struct
import zlib
from pathlib import Path

from app.config import settings

# name: (what a tester calls it, what it really is, its extension's type, the valid baseline?)
FILES: dict[str, dict] = {
    "id-photo.jpg": {"what": "an ordinary ID photo", "real": "jpg"},
    "id-scan.pdf": {"what": "an ordinary PDF scan", "real": "pdf"},
    "empty.jpg": {"what": "an empty file (0 bytes)", "real": "empty"},
    "big-photo.png": {"what": "a 25 MB photo", "real": "png", "mb": 25},
    "tiny.png": {"what": "a photo of 8 × 8 pixels, too small to read", "real": "png"},
    "sideways.jpg": {"what": "a photo taken sideways (rotated by its EXIF tag)", "real": "jpg"},
    "phone-photo.heic": {"what": "an iPhone photo (HEIC)", "real": "heic"},
    "program.jpg": {"what": "a program (.exe) renamed .jpg", "real": "exe"},
    "not-a.pdf": {"what": "a text file renamed .pdf", "real": "txt"},
    "drawing.svg": {"what": "an SVG image with a script inside", "real": "svg"},
    "መታወቂያ.png": {"what": "a file named in Ge'ez letters", "real": "png"},
    ("x" * 180 + ".png"): {"what": "a file with a 184-character name", "real": "png"},
}
VALID = "id-photo.jpg"
IMAGE = ("jpg", "jpeg", "png", "heic", "gif", "webp", "svg")

JPEG = base64.b64decode(  # a valid 16 × 12 JPEG, small enough to keep here
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAoHBwgHBgoICAgLCgoLDhgQDg0NDh0VFhEYIx8lJCIfIiEmKzcvJik0KSEiMEExNDk7Pj4+JS5ESUM8"
    "SDc9Pjv/2wBDAQoLCw4NDhwQEBw7KCIoOzs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozv/wAARCAAMABAD"
    "ASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKB"
    "kaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZ"
    "mqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQF"
    "BgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5"
    "OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX"
    "2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwC9RRRXinpn/9k=")


def _png(width: int, height: int, noise: bool = False) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    row = width * 3
    raw = b"".join(b"\x00" + (os.urandom(row) if noise else b"\x80" * row) for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 0 if noise else 6)) + chunk(b"IEND", b""))


def _sideways(jpeg: bytes) -> bytes:
    """The JPEG with an EXIF orientation of 6 (rotate 90°), as phones write it."""
    tiff = b"MM\x00\x2a\x00\x00\x00\x08" + struct.pack(">H", 1) + struct.pack(">HHII", 0x0112, 3, 1, 6 << 16) + b"\x00\x00\x00\x00"
    app1 = b"Exif\x00\x00" + tiff
    return jpeg[:2] + b"\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1 + jpeg[2:]


PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
       b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 200]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")


def _content(name: str) -> bytes:
    if name == "id-photo.jpg":
        return JPEG
    if name == "id-scan.pdf":
        return PDF
    if name == "empty.jpg":
        return b""
    if name == "big-photo.png":
        return _png(2900, 2900, noise=True)  # about 25 MB, too random to compress
    if name == "tiny.png":
        return _png(8, 8)
    if name == "sideways.jpg":
        return _sideways(JPEG)
    if name == "phone-photo.heic":
        return b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 64
    if name == "program.jpg":
        return b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff" + b"\x00" * 200 + b"This program cannot be run in DOS mode."
    if name == "not-a.pdf":
        return b"This is a plain text file, not a PDF.\n"
    if name == "drawing.svg":
        return b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><script>alert(document.domain)</script></svg>'
    return _png(64, 40)  # the odd names: an ordinary small picture


def folder() -> Path:
    return Path(settings.evidence_dir) / "tricky-files"


def path_of(name: str) -> Path | None:
    """The file, made on first use; None for a name Mirmera doesn't have."""
    if name not in FILES:
        return None
    path = folder() / name
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_content(name))
    return path


def name_of(value: str | None) -> str | None:
    """'@file:big-photo.png' -> 'big-photo.png' (a value naming one of Mirmera's files)."""
    if value and value.startswith("@file:") and value[6:] in FILES:
        return value[6:]
    return None


FIRST = ("empty.jpg", "big-photo.png", "program.jpg", "not-a.pdf", "phone-photo.heic")


def ideas() -> list[tuple[str, str]]:
    """The tricky files, the ones most likely to break an upload rule first; the ordinary PDF scan last."""
    order = list(FIRST) + [n for n in FILES if n not in FIRST and n not in (VALID, "id-scan.pdf")] + ["id-scan.pdf"]
    return [(FILES[n]["what"], f"@file:{n}") for n in order]


def size_mb(name: str) -> float:
    return FILES[name].get("mb") or (path_of(name).stat().st_size / 1_048_576)
