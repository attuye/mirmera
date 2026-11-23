"""The privacy check on uploads: Mirmera's geotagged probe, read back from any image the app serves."""
import struct

from app.agent import exif, files


def test_the_probe_carries_a_location_and_a_camera_and_is_still_a_photo():
    photo = exif.geotagged(files.JPEG)
    g = exif.gps_of(photo)
    assert g == {"lat": 9.03, "lon": 38.76, "camera": "Mirmera GPS probe", "probe": True}
    assert exif.place(g) == "9.0300° N, 38.7600° E"
    assert photo.startswith(b"\xff\xd8") and photo.endswith(b"\xff\xd9")
    assert files.path_of("geotagged.jpg").read_bytes() == photo
    assert ("a phone photo with its GPS location still in it (is it served back with it?)", "@file:geotagged.jpg") in files.ideas()


def test_photos_without_a_location_say_nothing():
    assert exif.gps_of(files.JPEG) is None  # no EXIF at all
    assert exif.gps_of(files._sideways(files.JPEG)) is None  # EXIF, but only the orientation
    assert exif.gps_of(exif.without_exif(exif.geotagged(files.JPEG))) is None  # what the app should serve
    assert exif.gps_of(files._png(10, 10)) is None
    for junk in (b"", b"Exif\x00\x00", b"Exif\x00\x00MM\x00\x2a\xff\xff\xff\xff", b"\xff\xd8" + b"\x00" * 50):
        assert exif.gps_of(junk) is None  # odd data never raises


def test_someone_elses_location_south_and_west_little_endian():
    """Not Mirmera's photo: another phone, another place, the other byte order."""
    end = "<"
    entries = [(1, 2, 2, b"S\x00\x00\x00"), (2, 5, 3, None), (3, 2, 2, b"W\x00\x00\x00"), (4, 5, 3, None)]
    gps_at, lat_at = 8 + 18, 8 + 18 + 54
    ifd0 = struct.pack(end + "H", 1) + struct.pack(end + "HHII", exif.TAG_GPS, 4, 1, gps_at) + b"\x00" * 4
    gps = struct.pack(end + "H", 4)
    for tag, kind, count, raw in entries:
        gps += struct.pack(end + "HHI", tag, kind, count) + (raw or struct.pack(end + "I", lat_at if tag == 2 else lat_at + 24))
    gps += b"\x00" * 4
    dms = lambda d, m, s: b"".join(struct.pack(end + "II", v, 1) for v in (d, m, s))  # noqa: E731
    tiff = b"II\x2a\x00" + struct.pack(end + "I", 8) + ifd0 + gps + dms(33, 52, 0) + dms(151, 12, 36)
    g = exif.gps_of(b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x10eXIf" + tiff)
    assert g["probe"] is False and g["camera"] == ""
    assert exif.place(g) == "33.8667° S, 151.2100° W"
