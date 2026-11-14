"""Extraction des images d'un GIF animé à modifier (_extract_gif_frames).

Une image qui ne se lit pas arrête la lecture (un GIF se lit dans l'ordre) :
les images lues sont gardées et l'arrêt est rendu, jamais avalé — la fenêtre
s'ouvrirait sinon avec un GIF tronqué sans prévenir.

animated_gif_dialog_qt.py importe PySide6 au niveau module : la fonction est
extraite par AST et exécutée seule, sans aucun import Qt.
"""
import io
import os
import threading

import pytest
from PIL import Image, ImageDraw

import ast_loader

extract_gif_frames = ast_loader.load("animated_gif_dialog_qt.py", {"_extract_gif_frames"},
                                     io=io, os=os, Image=Image)["_extract_gif_frames"]


def gif_bytes(count=10):
    frames = []
    for i in range(count):
        im = Image.new("RGB", (64, 64), (255, 255, 255))
        ImageDraw.Draw(im).rectangle([i * 5, i * 5, i * 5 + 12, i * 5 + 12], fill=(i * 25, 0, 255 - i * 25))
        frames.append(im)
    buf = io.BytesIO()
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:], duration=100)
    return buf.getvalue()


def entry(data):
    return {"orig_name": "anim.gif", "bytes": data, "gif_durations": [100] * 10}


def test_complete_gif_is_read_entirely():
    frames, stopped = extract_gif_frames(entry(gif_bytes()))
    assert len(frames) == 10 and not stopped
    assert frames[3]["orig_name"] == "anim_frame_003.png"


def test_opaque_frames_stay_rgb_so_that_their_colors_are_kept():
    """En RGBA, Pillow réduit les couleurs moins fidèlement : un GIF modifié
    perdait des couleurs sans que rien n'ait été changé."""
    frames, _stopped = extract_gif_frames(entry(gif_bytes()))
    assert {f["img"].mode for f in frames} == {"RGB"}


def test_any_transparent_frame_keeps_every_frame_in_rgba():
    """La seconde image est opaque, et passe en RGBA comme la première."""
    half = Image.new("RGBA", (8, 8), (0, 0, 255, 255))
    half.paste((0, 0, 0, 0), (0, 0, 4, 8))
    images = [half, Image.new("RGBA", (8, 8), (255, 0, 0, 255))]
    buf = io.BytesIO()
    images[0].save(buf, "GIF", save_all=True, append_images=images[1:], duration=100, disposal=2)
    frames, _stopped = extract_gif_frames(
        {"orig_name": "anim.gif", "bytes": buf.getvalue(), "gif_durations": [100, 100]})
    assert {f["img"].mode for f in frames} == {"RGBA"}
    assert frames[0]["img"].getpixel((1, 1))[3] == 0
    assert frames[1]["img"].getpixel((1, 1))[3] == 255


def test_reading_stopped_before_the_end_is_reported():
    data = gif_bytes()
    starts = [i for i in range(14, len(data)) if data[i] == 0x2C]
    frames, stopped = extract_gif_frames(entry(data[:starts[5] + 20]))
    assert stopped
    assert len(frames) == 5


def test_unreadable_gif_gives_no_frame():
    frames, stopped = extract_gif_frames(entry(b"GIF89a not really a gif"))
    assert frames == [] and stopped


def test_progress_is_reported_for_each_frame():
    seen = []
    extract_gif_frames(entry(gif_bytes()), on_frame=seen.append)
    assert seen == list(range(1, 11))


def test_extraction_stopped_on_request_gives_nothing_and_frees_the_images(monkeypatch):
    """Annuler ou fichier fermé pendant l'extraction (thread de « Modifier »)."""
    read, closed = [], []
    real_close = Image.Image.close

    def close(self):
        closed.append(self)
        real_close(self)

    monkeypatch.setattr(Image.Image, "close", close)
    result = extract_gif_frames(entry(gif_bytes()), on_frame=read.append,
                                should_stop=lambda: len(read) >= 3)
    assert result is None and read == [1, 2, 3]
    assert len([im for im in closed if im.size == (64, 64)]) >= 3


# ── Thread d'extraction ──────────────────────────────────────────────────────

class _Signal:
    def __init__(self):
        self.emitted = []

    def emit(self, *args):
        self.emitted.append(args)


def extract_worker(extract):
    cls = ast_loader.load_methods("animated_gif_dialog_qt.py", "_GifExtractWorker", {"run", "_run"},
                                  _extract_gif_frames=extract)
    worker = cls()
    worker.progress, worker.done = _Signal(), _Signal()
    worker._gif, worker._total = entry(gif_bytes()), 10
    worker._cancelled = threading.Event()
    return worker


def test_extract_worker_gives_the_frames_and_their_progress():
    worker = extract_worker(extract_gif_frames)
    worker.run()
    [(result,)] = worker.done.emitted
    assert len(result[0]) == 10 and not result[1]
    assert worker.progress.emitted[-1] == (10, 10)


def test_extract_worker_always_signals_its_end():
    """Erreur imprévue : done(None) quand même, sinon le texte de progression
    resterait sur la mosaïque ; l'erreur est relancée pour le journal."""
    def broken(*_args, **_kwargs):
        raise RuntimeError("Aaaa")

    worker = extract_worker(broken)
    with pytest.raises(RuntimeError):
        worker.run()
    assert worker.done.emitted == [(None,)]
