"""Extraction des images d'un GIF animé à modifier (_extract_gif_frames).

Une image qui ne se lit pas arrête la lecture (un GIF se lit dans l'ordre) :
les images lues sont gardées et l'arrêt est rendu, jamais avalé — la fenêtre
s'ouvrirait sinon avec un GIF tronqué sans prévenir.

animated_gif_dialog_qt.py importe PySide6 au niveau module : la fonction est
extraite par AST et exécutée seule, sans aucun import Qt.
"""
import io
import os

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
    assert frames[3]["img"].mode == "RGBA"


def test_reading_stopped_before_the_end_is_reported():
    data = gif_bytes()
    starts = [i for i in range(14, len(data)) if data[i] == 0x2C]
    frames, stopped = extract_gif_frames(entry(data[:starts[5] + 20]))
    assert stopped
    assert len(frames) == 5


def test_unreadable_gif_gives_no_frame():
    frames, stopped = extract_gif_frames(entry(b"GIF89a not really a gif"))
    assert frames == [] and stopped
