"""The privacy check on uploads: a photo the app serves back must not still say where it was taken.

Phones write the place a photo was taken (GPS) and the phone's make and model into the photo itself, as
EXIF. In KYC that is the customer's home, more often than not. An app that stores an uploaded selfie or ID
photo and serves it back (a preview, a reviewer's screen, a profile picture) without removing the EXIF
gives that location to anyone who can see the picture.

Mirmera's upload ideas include a photo with a known location in it (Addis Ababa, camera "Mirmera GPS
probe"). Every image the app sends to the browser is read here, in pure Python: one that still holds a GPS
location is a bug draft. If it's Mirmera's location, the upload went through untouched; any other location
is someone's photo carrying theirs.
"""
import struct

PROBE_MAKE, PROBE_MODEL = "Mirmera", "GPS probe"
PROBE_LAT, PROBE_LON = (9, 1, 48.0), (38, 45, 36.0)  # 9.03° N, 38.76° E: Addis Ababa
IMAGE_TYPES = ("image/jpeg", "image/jpg", "image/png", "image/webp", "image/tiff", "image/heic", "image/avif")

TAG_MAKE, TAG_MODEL, TAG_GPS = 0x010F, 0x0110, 0x8825
GPS_LAT_REF, GPS_LAT, GPS_LON_REF, GPS_LON = 1, 2, 3, 4


# ---------- reading ----------

def _tiff(data: bytes) -> bytes | None:
    """The EXIF block (a little TIFF file) inside a JPEG, PNG or WebP, or None."""
    at = data.find(b"Exif\x00\x00")
    if at >= 0:
        return data[at + 6:]
    at = data.find(b"eXIf")  # PNG's chunk: the TIFF follows the chunk type directly
    if at >= 0 and data.startswith(b"\x89PNG"):
        return data[at + 4:]
    return None


def _entries(tiff: bytes, offset: int, end: str) -> dict[int, tuple[int, int, bytes]]:
    """An IFD's entries: tag -> (type, count, the 4 value-or-offset bytes)."""
    if offset + 2 > len(tiff):
        return {}
    (n,) = struct.unpack(end + "H", tiff[offset:offset + 2])
    out = {}
    for i in range(min(n, 200)):
        at = offset + 2 + i * 12
        if at + 12 > len(tiff):
            break
        tag, kind, count = struct.unpack(end + "HHI", tiff[at:at + 8])
        out[tag] = (kind, count, tiff[at + 8:at + 12])
    return out


def _ascii(tiff: bytes, entry, end: str) -> str:
    kind, count, raw = entry
    data = raw[:count] if count <= 4 else tiff[struct.unpack(end + "I", raw)[0]:][:count]
    return data.split(b"\x00")[0].decode("latin-1", "replace").strip()


def _degrees(tiff: bytes, entry, end: str) -> float | None:
    kind, count, raw = entry
    if kind != 5 or count < 3:  # three RATIONALs: degrees, minutes, seconds
        return None
    at = struct.unpack(end + "I", raw)[0]
    parts = []
    for i in range(3):
        if at + 8 * i + 8 > len(tiff):
            return None
        num, den = struct.unpack(end + "II", tiff[at + 8 * i:at + 8 * i + 8])
        parts.append(num / den if den else 0.0)
    return parts[0] + parts[1] / 60 + parts[2] / 3600


def gps_of(data: bytes) -> dict | None:
    """The GPS location (and camera) an image still carries, or None. Never raises on odd data."""
    try:
        tiff = _tiff(data)
        if not tiff or tiff[:2] not in (b"II", b"MM"):
            return None
        end = "<" if tiff[:2] == b"II" else ">"
        (ifd0,) = struct.unpack(end + "I", tiff[4:8])
        main = _entries(tiff, ifd0, end)
        if TAG_GPS not in main:
            return None
        gps = _entries(tiff, struct.unpack(end + "I", main[TAG_GPS][2])[0], end)
        lat = _degrees(tiff, gps[GPS_LAT], end) if GPS_LAT in gps else None
        lon = _degrees(tiff, gps[GPS_LON], end) if GPS_LON in gps else None
        if lat is None or lon is None:
            return None
        if GPS_LAT_REF in gps and gps[GPS_LAT_REF][2][:1] == b"S":
            lat = -lat
        if GPS_LON_REF in gps and gps[GPS_LON_REF][2][:1] == b"W":
            lon = -lon
        make = _ascii(tiff, main[TAG_MAKE], end) if TAG_MAKE in main else ""
        model = _ascii(tiff, main[TAG_MODEL], end) if TAG_MODEL in main else ""
        return {"lat": round(lat, 5), "lon": round(lon, 5), "camera": " ".join(x for x in (make, model) if x),
                "probe": make == PROBE_MAKE and model == PROBE_MODEL}
    except Exception:
        return None


def place(g: dict) -> str:
    return f"{abs(g['lat']):.4f}° {'N' if g['lat'] >= 0 else 'S'}, {abs(g['lon']):.4f}° {'E' if g['lon'] >= 0 else 'W'}"


# ---------- writing Mirmera's probe ----------

def geotagged(jpeg: bytes) -> bytes:
    """The JPEG with EXIF as a phone writes it: make, model and a GPS location (Mirmera's probe)."""
    end = ">"
    make, model = PROBE_MAKE.encode() + b"\x00", PROBE_MODEL.encode() + b"\x00"

    def rationals(dms):
        return b"".join(struct.pack(end + "II", int(round(v * 100)), 100) for v in dms)

    # Layout: header (8) | IFD0: 3 entries (2 + 36 + 4 = 42) | make | model | GPS IFD: 4 entries (2 + 48 + 4) | lat | lon
    ifd0_at = 8
    make_at = ifd0_at + 42
    model_at = make_at + len(make)
    gps_at = model_at + len(model)
    lat_at = gps_at + 54
    lon_at = lat_at + 24
    ifd0 = struct.pack(end + "H", 3) + b"".join([
        struct.pack(end + "HHII", TAG_MAKE, 2, len(make), make_at),
        struct.pack(end + "HHII", TAG_MODEL, 2, len(model), model_at),
        struct.pack(end + "HHII", TAG_GPS, 4, 1, gps_at),
    ]) + b"\x00\x00\x00\x00"
    gps = struct.pack(end + "H", 4) + b"".join([
        struct.pack(end + "HHI", GPS_LAT_REF, 2, 2) + b"N\x00\x00\x00",
        struct.pack(end + "HHII", GPS_LAT, 5, 3, lat_at),
        struct.pack(end + "HHI", GPS_LON_REF, 2, 2) + b"E\x00\x00\x00",
        struct.pack(end + "HHII", GPS_LON, 5, 3, lon_at),
    ]) + b"\x00\x00\x00\x00"
    tiff = b"MM\x00\x2a" + struct.pack(end + "I", ifd0_at) + ifd0 + make + model + gps \
        + rationals(PROBE_LAT) + rationals(PROBE_LON)
    app1 = b"Exif\x00\x00" + tiff
    return jpeg[:2] + b"\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1 + jpeg[2:]


def without_exif(jpeg: bytes) -> bytes:
    """The JPEG with its EXIF segment removed: what the app should serve (used by tests)."""
    at = jpeg.find(b"\xff\xe1")
    if at < 0:
        return jpeg
    (length,) = struct.unpack(">H", jpeg[at + 2:at + 4])
    return jpeg[:at] + jpeg[at + 2 + length:]
