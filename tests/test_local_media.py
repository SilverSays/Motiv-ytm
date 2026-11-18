"""Fase 8.5: archivos de audio del disco (etiquetas, escaneo y reproducción directa)."""

import os
import struct

import pytest

from ytm_native.core import config
from ytm_native.core.db import Track
from ytm_native.services import local_media as lm

JPEG = b"\xff\xd8\xff\xe0" + b"caratula-de-prueba" * 4


# ---------------------------------------------------------------------------
# Constructores de archivos de prueba (bytes mínimos de cada formato)
# ---------------------------------------------------------------------------
def syncsafe(n: int) -> bytes:
    return bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F])


def id3v23(frames: list[tuple[bytes, bytes]]) -> bytes:
    body = b"".join(fid + struct.pack(">I", len(data)) + b"\x00\x00" + data for fid, data in frames)
    body += b"\x00" * 16                              # relleno
    return b"ID3\x03\x00\x00" + syncsafe(len(body)) + body


def id3v24(frames: list[tuple[bytes, bytes]]) -> bytes:
    body = b"".join(fid + syncsafe(len(data)) + b"\x00\x00" + data for fid, data in frames)
    return b"ID3\x04\x00\x00" + syncsafe(len(body)) + body


def mpeg_frame(xing_frames: int = 0) -> bytes:
    header = b"\xff\xfb\x90\x64"                      # MPEG-1 capa III, 128 kbps, 44,1 kHz, estéreo
    side = b"\x00" * 32
    xing = b"Xing" + struct.pack(">II", 1, xing_frames) if xing_frames else b""
    frame = header + side + xing
    return frame + b"\x00" * (417 - len(frame))


def make_mp3(path, title="Título", artist="Artista", album="Disco", frames=1000, cover=True):
    tags = [
        (b"TIT2", b"\x01" + title.encode("utf-16")),
        (b"TPE1", b"\x00" + artist.encode("latin-1")),
        (b"TALB", b"\x03" + album.encode("utf-8")),
    ]
    if cover:
        tags.append((b"APIC", b"\x00image/jpeg\x00\x03portada\x00" + JPEG))
    path.write_bytes(id3v23(tags) + mpeg_frame(frames) + b"\x00" * 2000)
    return path


def vorbis_block(comments: dict, framing: bool = False) -> bytes:
    vendor = b"prueba"
    entries = [f"{k}={v}".encode("utf-8") for k, v in comments.items()]
    data = struct.pack("<I", len(vendor)) + vendor + struct.pack("<I", len(entries))
    data += b"".join(struct.pack("<I", len(e)) + e for e in entries)
    return data + (b"\x01" if framing else b"")


def flac_picture(image: bytes) -> bytes:
    mime = b"image/jpeg"
    return (struct.pack(">II", 3, len(mime)) + mime + struct.pack(">I", 0)
            + struct.pack(">IIII", 1, 1, 24, 0) + struct.pack(">I", len(image)) + image)


def make_flac(path, seconds=3, comments=None, cover=True):
    rate, total = 44100, 44100 * seconds
    packed = (rate << 44) | (1 << 41) | (15 << 36) | total
    info = struct.pack(">HH", 4096, 4096) + b"\x00" * 6 + packed.to_bytes(8, "big") + b"\x00" * 16
    blocks = [(0, info), (4, vorbis_block(comments or {"TITLE": "Flac", "ARTIST": "Uno", "ALBUM": "Sin"}))]
    if cover:
        blocks.append((6, flac_picture(JPEG)))
    out = b"fLaC"
    for i, (kind, data) in enumerate(blocks):
        last = 0x80 if i == len(blocks) - 1 else 0
        out += bytes([last | kind]) + len(data).to_bytes(3, "big") + data
    path.write_bytes(out + b"\x00" * 100)
    return path


def ogg_page(packet: bytes, granule: int, seq: int, kind: int = 0) -> bytes:
    lacing = [255] * (len(packet) // 255) + [len(packet) % 255]
    return (b"OggS" + bytes([0, kind]) + struct.pack("<qIII", granule, 1, seq, 0)
            + bytes([len(lacing)]) + bytes(lacing) + packet)


def make_opus(path, seconds=10, comments=None):
    head = b"OpusHead" + bytes([1, 2]) + struct.pack("<HIhB", 312, 48000, 0, 0)
    tags = b"OpusTags" + vorbis_block(comments or {"title": "Opus", "artist": "Dos", "album": "Ogg"})
    audio = ogg_page(b"\x00" * 50, 48000 * seconds + 312, 2)
    path.write_bytes(ogg_page(head, 0, 0, 2) + ogg_page(tags, 0, 1) + audio)
    return path


def make_vorbis(path, seconds=4):
    ident = b"\x01vorbis" + struct.pack("<IBI", 0, 2, 44100) + b"\x00" * 13
    comments = b"\x03vorbis" + vorbis_block({"TITLE": "Vorbis", "ARTIST": "Tres"}, framing=True)
    path.write_bytes(ogg_page(ident, 0, 0, 2) + ogg_page(comments, 0, 1)
                     + ogg_page(b"\x00" * 30, 44100 * seconds, 2))
    return path


def atom(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def data_atom(value: bytes, kind: int = 1) -> bytes:
    return atom(b"data", struct.pack(">II", kind, 0) + value)


def make_m4a(path, seconds=5):
    mvhd = atom(b"mvhd", b"\x00" * 12 + struct.pack(">II", 1000, seconds * 1000) + b"\x00" * 80)
    ilst = atom(b"ilst", atom(b"\xa9nam", data_atom("Mp4 título".encode())) +
                atom(b"\xa9ART", data_atom(b"Cuatro")) + atom(b"\xa9alb", data_atom(b"Caja")) +
                atom(b"covr", data_atom(JPEG, 13)))
    meta = atom(b"meta", b"\x00" * 4 + atom(b"hdlr", b"\x00" * 25) + ilst)
    moov = atom(b"moov", mvhd + atom(b"udta", meta))
    path.write_bytes(atom(b"ftyp", b"M4A \x00\x00\x00\x00") + moov + atom(b"mdat", b"\x00" * 64))
    return path


def make_wav(path, seconds=2):
    fmt = struct.pack("<HHIIHH", 1, 2, 44100, 176400, 4, 16)
    info = b"INFO" + b"INAM" + struct.pack("<I", 4) + b"Wav\x00" + b"IART" + struct.pack("<I", 6) + b"Cinco\x00"
    data = b"\x00" * (176400 * seconds)
    chunks = atom_le(b"fmt ", fmt) + atom_le(b"LIST", info) + atom_le(b"data", data)
    path.write_bytes(b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks)
    return path


def atom_le(kind: bytes, payload: bytes) -> bytes:
    return kind + struct.pack("<I", len(payload)) + payload + (b"\x00" if len(payload) & 1 else b"")


def make_adts(path, frames=430):
    length = 200
    header = bytes([0xFF, 0xF1, (1 << 6) | (4 << 2), 0x80 | (length >> 11),
                    (length >> 3) & 0xFF, ((length & 7) << 5) | 0x1F, 0xFC])
    path.write_bytes((header + b"\x00" * (length - 7)) * frames)
    return path


# ---------------------------------------------------------------------------
# Etiquetas
# ---------------------------------------------------------------------------
def test_mp3_id3v23_with_cover_and_xing_duration(tmp_path):
    tags = lm.read_tags(make_mp3(tmp_path / "a.mp3"))
    assert (tags.title, tags.artists, tags.album) == ("Título", ["Artista"], "Disco")
    assert tags.duration_s == pytest.approx(1000 * 1152 / 44100)
    assert tags.cover == JPEG and tags.cover_mime == "image/jpeg"


def test_mp3_id3v24_utf8_with_several_artists(tmp_path):
    frames = [(b"TIT2", b"\x03" + "Canción ñ".encode()), (b"TPE1", b"\x03Ana\x00Vera"),
              (b"TLEN", b"\x00" + b"183000")]
    path = tmp_path / "b.mp3"
    path.write_bytes(id3v24(frames) + mpeg_frame() + b"\x00" * 1000)
    tags = lm.read_tags(path)
    assert tags.title == "Canción ñ" and tags.artists == ["Ana", "Vera"]
    assert tags.duration_s == pytest.approx(183.0)            # TLEN manda sobre la estimación


def test_mp3_without_xing_estimates_from_bitrate(tmp_path):
    path = tmp_path / "cbr.mp3"
    path.write_bytes(mpeg_frame() + b"\x00" * (16000 * 10 - 417))   # 10 s a 128 kbps
    assert lm.read_tags(path).duration_s == pytest.approx(10.0, abs=0.05)


def test_id3v1_fallback(tmp_path):
    tag = b"TAG" + b"Viejo".ljust(30, b"\x00") + b"Antiguo".ljust(30, b"\x00") + b"Album".ljust(30, b"\x00")
    tag += b"\x00" * (128 - len(tag))
    path = tmp_path / "v1.mp3"
    path.write_bytes(mpeg_frame() + b"\x00" * 3000 + tag)
    tags = lm.read_tags(path)
    assert (tags.title, tags.artists, tags.album) == ("Viejo", ["Antiguo"], "Album")


def test_flac_streaminfo_comments_and_picture(tmp_path):
    tags = lm.read_tags(make_flac(tmp_path / "c.flac"))
    assert (tags.title, tags.artists, tags.album) == ("Flac", ["Uno"], "Sin")
    assert tags.duration_s == pytest.approx(3.0) and tags.cover == JPEG


def test_ogg_opus_and_vorbis(tmp_path):
    opus = lm.read_tags(make_opus(tmp_path / "d.opus"))
    assert (opus.title, opus.artists, opus.album) == ("Opus", ["Dos"], "Ogg")
    assert opus.duration_s == pytest.approx(10.0)
    vorbis = lm.read_tags(make_vorbis(tmp_path / "e.ogg"))
    assert (vorbis.title, vorbis.artists) == ("Vorbis", ["Tres"]) and vorbis.duration_s == pytest.approx(4.0)


def test_m4a_atoms(tmp_path):
    tags = lm.read_tags(make_m4a(tmp_path / "f.m4a"))
    assert (tags.title, tags.artists, tags.album) == ("Mp4 título", ["Cuatro"], "Caja")
    assert tags.duration_s == pytest.approx(5.0) and tags.cover == JPEG


def test_wav_info_and_duration(tmp_path):
    tags = lm.read_tags(make_wav(tmp_path / "g.wav"))
    assert (tags.title, tags.artists) == ("Wav", ["Cinco"]) and tags.duration_s == pytest.approx(2.0)


def test_raw_aac_counts_adts_frames(tmp_path):
    tags = lm.read_tags(make_adts(tmp_path / "h.aac", frames=430))
    assert tags.duration_s == pytest.approx(430 * 1024 / 44100)


@pytest.mark.parametrize("payload", [b"", b"ID3\x03\x00\x00\x7f\x7f\x7f\x7f", b"fLaC\x00", b"OggS" + b"\xff" * 30,
                                     b"\x00\x00\x00\x20ftypM4A " + b"\xff" * 40, b"RIFF\x00\x00\x00\x00WAVE",
                                     os.urandom(4000)])
def test_damaged_files_never_raise(tmp_path, payload):
    path = tmp_path / "roto.flac"
    path.write_bytes(payload)
    tags = lm.read_tags(path)
    track = lm.track_from_file(path, tags)
    assert track.title == "roto" and track.video_id.startswith("local:")


def test_ids_are_stable_and_case_insensitive_on_windows(tmp_path):
    path = tmp_path / "Pista.mp3"
    first = lm.local_id(path)
    assert first.startswith("local:") and len(first) == len("local:") + 40
    assert first == lm.local_id(str(path))
    if os.name == "nt":
        assert first == lm.local_id(str(path).upper())
    assert lm.file_uri(path).startswith("file:///")


# ---------------------------------------------------------------------------
# Escaneo
# ---------------------------------------------------------------------------
@pytest.fixture
def library(tmp_path):
    root = tmp_path / "Música"
    (root / "Rock" / "Disco 1").mkdir(parents=True)
    (root / ".oculta").mkdir()
    make_mp3(root / "Rock" / "Disco 1" / "01 uno.mp3", title="Uno")
    make_flac(root / "Rock" / "02 dos.flac")
    make_opus(root / "tres.opus")
    make_mp3(root / ".oculta" / "no.mp3")
    (root / "notas.txt").write_text("no es audio", encoding="utf-8")
    (root / "portada.jpg").write_bytes(JPEG)
    return root


def test_scan_reads_tags_assigns_local_ids_and_skips_hidden(db, library, tmp_path):
    report = lm.scan_folder(db, library, covers=tmp_path / "covers")
    assert (report.total, report.read, report.removed) == (3, 3, 0)
    tracks = db.local_tracks()
    assert sorted(t.title for t in tracks) == ["Flac", "Opus", "Uno"]
    assert all(t.video_id.startswith("local:") for t in tracks)
    uno = next(t for t in tracks if t.title == "Uno")
    assert uno.artists == "Artista" and uno.duration_s == round(1000 * 1152 / 44100)
    assert uno.thumb_url.startswith("file:///")
    from PySide6.QtCore import QUrl

    assert open(QUrl(uno.thumb_url).toLocalFile(), "rb").read() == JPEG
    path = db.local_file_path(uno.video_id)
    assert path.endswith("01 uno.mp3") and os.path.isfile(path)
    assert db.local_track_count() == 3


def test_rescan_reads_only_what_changed_and_forgets_what_is_gone(db, library, tmp_path):
    covers = tmp_path / "covers"
    lm.scan_folder(db, library, covers=covers)
    again = lm.scan_folder(db, library, covers=covers)
    assert (again.total, again.read, again.removed) == (3, 0, 0)          # nada que releer
    target = library / "tres.opus"
    make_opus(target, comments={"title": "Tres nuevo", "artist": "Dos"})
    os.utime(target, (target.stat().st_atime, target.stat().st_mtime + 10))
    (library / "Rock" / "02 dos.flac").unlink()
    after = lm.scan_folder(db, library, covers=covers)
    assert (after.read, after.removed) == (1, 1)
    titles = sorted(t.title for t in db.local_tracks())
    assert titles == ["Tres nuevo", "Uno"]
    assert len(list(covers.glob("*"))) == 1                               # la carátula del borrado se fue


def test_scan_can_be_cancelled(db, library, tmp_path):
    import threading

    cancel = threading.Event()
    cancel.set()
    report = lm.scan_folder(db, library, cancel=cancel, covers=tmp_path / "covers")
    assert report.cancelled and db.local_track_count() == 0


def test_changing_folder_forgets_the_old_one(db, library, tmp_path):
    lm.scan_folder(db, library, covers=tmp_path / "covers")
    other = tmp_path / "Otra"
    other.mkdir()
    db.forget_local_folder(lm.normalized_path(other))
    assert db.local_track_count() == 0


# ---------------------------------------------------------------------------
# Reproducción directa en mpv, sin yt-dlp
# ---------------------------------------------------------------------------
from test_player import FakeExtractor, FakeMpv, settle, wait_until  # noqa: E402


@pytest.fixture
def local_env(qapp, db, library, tmp_path, monkeypatch):
    from ytm_native.playback.player import PlaybackController

    monkeypatch.setattr(config, "FIRST_LOAD_MIN_URL_AGE_S", 0.0)
    monkeypatch.setattr(config, "PREFETCH_AHEAD", 3)
    monkeypatch.setattr(config, "PREFETCH_START_DELAY_MS", 0)
    lm.scan_folder(db, library, covers=tmp_path / "covers")
    mpv, extractor = FakeMpv(), FakeExtractor()
    radio_calls = []
    player = PlaybackController(db, extractor, mpv, radio_fetcher=lambda vid: radio_calls.append(vid) or [])
    notices = []
    player.notice.connect(notices.append)
    yield type("Env", (), dict(qapp=qapp, db=db, mpv=mpv, ex=extractor, player=player,
                               notices=notices, radio=radio_calls, tracks=db.local_tracks()))
    player.stop()


def test_local_track_goes_straight_to_mpv_without_the_extractor(local_env):
    env = local_env
    env.player.play_tracks(env.tracks, 0)
    assert wait_until(env.qapp, lambda: env.mpv.loaded)
    url = env.mpv.loaded[0]
    assert url.startswith("file:///") and env.ex.calls == []
    env.mpv.begin(26.0)
    settle(env.qapp)
    assert env.ex.calls == []                     # tampoco se resuelven por adelantado


def test_mixed_queue_prefetches_only_youtube_tracks(local_env):
    env = local_env
    yt = Track("yt1", "De YouTube", "Alguien", "", 100)
    env.db.upsert_track(yt)
    env.player.play_tracks([env.tracks[0], env.tracks[1], yt], 0)
    assert wait_until(env.qapp, lambda: env.mpv.loaded)
    env.mpv.begin(26.0)
    assert wait_until(env.qapp, lambda: env.ex.calls)
    assert {vid for vid, _ in env.ex.calls} == {"yt1"}


def test_missing_file_is_reported_without_network(local_env, tmp_path):
    env = local_env
    track = env.tracks[0]
    os.remove(env.db.local_file_path(track.video_id))
    env.player.play_tracks([track], 0)
    assert wait_until(env.qapp, lambda: env.notices)
    assert env.ex.calls == [] and env.mpv.loaded == []
    assert "No disponible" in env.notices[0]


def test_radio_never_seeds_from_a_local_track(local_env):
    env = local_env
    env.player.start_radio(env.tracks[0])
    settle(env.qapp)
    assert env.radio == []


def test_local_tracks_are_not_scrobbled_nor_reported(db, qapp):
    from ytm_native.services.account_actions import AccountActions
    from ytm_native.services.lastfm import scrobble_from

    track = Track("local:abc", "Del disco", "Yo", "", 200)
    assert scrobble_from(track, 0.0, 200.0) is None
    db.set_setting(config.ACCOUNT_MUTATIONS_KEY, "1")
    db.set_setting(config.ACCOUNT_HISTORY_KEY, "1")
    ran = []
    assert AccountActions(db, runner=lambda fn, done: ran.append(fn)).report_history(track) is False
    assert ran == []


def test_local_art_urls_never_point_to_youtube(db):
    from ytm_native.playback.media_controls import default_art_url
    from ytm_native.ui.art import track_art_url

    assert track_art_url("local:abc", "", 120) == ""
    assert track_art_url("local:abc", "file:///C:/x.jpg", 120) == "file:///C:/x.jpg"
    assert default_art_url(Track("local:abc", "x", thumb_url="file:///C:/x.jpg")) == ""
    playlist_id = db.create_local_playlist("Mixta", [Track("local:abc", "x")])
    assert db.playlist_cover(db.get_playlist(playlist_id)) == ""


def test_art_loader_reads_local_covers_from_disk(qapp, tmp_path):
    from PySide6.QtGui import QColor, QImage

    from ytm_native.ui.art import ArtLoader

    image = QImage(1200, 800, QImage.Format.Format_RGB32)
    image.fill(QColor("#3366aa"))
    path = tmp_path / "cover.png"
    image.save(str(path))
    loader = ArtLoader(cache_dir=tmp_path / "cache")
    loaded = loader.image(path.as_uri())
    assert loaded is not None and max(loaded.width(), loaded.height()) == ArtLoader.LOCAL_ART_PX
    assert not (tmp_path / "cache").exists()               # sin copia en la caché de la red
    assert loader.image((tmp_path / "no.png").as_uri()) is None


# ---------------------------------------------------------------------------
# Ventana: la vista «En tu equipo»
# ---------------------------------------------------------------------------
from test_home_search import FakeNet, net, window  # noqa: E402,F401 - fixtures


def test_music_folder_view_lists_local_tracks(window, db, library, tmp_path):
    from ytm_native.ui import main_window as mw

    assert window._sidebar_row(mw.LOCAL_KEY) < 0           # sin carpeta no aparece
    db.set_setting(config.LOCAL_MUSIC_DIR_KEY, str(library))
    lm.scan_folder(db, library, covers=tmp_path / "covers")
    window._reload_sidebar()
    assert window._sidebar_row(mw.LOCAL_KEY) >= 0
    window._navigate(mw.LOCAL_KEY)
    assert window.model.rowCount() == 3
    assert window.band._full_title == "En tu equipo"


def test_background_scan_from_the_window(window, db, library):
    from test_home_search import wait_until as ui_wait
    from ytm_native.ui import main_window as mw

    db.set_setting(config.LOCAL_MUSIC_DIR_KEY, str(library))
    window._reload_sidebar()
    window._navigate(mw.LOCAL_KEY)
    window._scan_local()
    assert window._scanning
    assert ui_wait(lambda: not window._scanning, 5000)
    assert window.model.rowCount() == 3
