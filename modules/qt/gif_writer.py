"""
modules/qt/gif_writer.py — Assemblage d'un GIF animé, sans Qt.

Appelé dans le thread de création de la fenêtre GIF animé
(animated_gif_dialog_qt._GifWorker). Ne modifie jamais les pages reçues : la
fenêtre peut lire leurs dimensions pendant ce temps.
"""
import io
import os
import re

from PIL import Image

from modules.qt.entries import open_page_image, oriented_size, to_display_mode
from modules.qt.image_ops import has_several_frames

_ANIMATED_NAME = re.compile(r"^Animated_(\d+)\.gif$", re.IGNORECASE)

STEP_READING = "reading"
STEP_FRAME = "frame"
STEP_SAVING = "saving"


class GifCancelled(Exception):
    """Création arrêtée à la demande de l'utilisateur."""

    expected_error = True


class _PageUnreadable(Exception):
    """Une page lisible à l'en-tête ne se décode pas."""

    def __init__(self, page):
        super().__init__(page.get("orig_name", ""))
        self.page = page


def next_gif_name(images_data):
    """Nom du GIF à créer : numéro suivant le plus grand Animated_N.gif du
    document. Un simple comptage redonnerait un nom déjà pris après la
    suppression de Animated_1.gif."""
    numbers = []
    for entry in images_data:
        match = _ANIMATED_NAME.match(os.path.basename(entry.get("orig_name", "")))
        if match:
            numbers.append(int(match.group(1)))
    return f"Animated_{max(numbers, default=0) + 1}.gif"


def pages_left_out(pages):
    """(pages illisibles, absentes du GIF ; pages à plusieurs images, dont seule
    la première sera reprise), à annoncer avant la création."""
    unreadable, several = [], []
    for page in pages:
        if page.get("is_corrupted") or (page.get("bytes") is None and page.get("img") is None):
            unreadable.append(page)
        elif has_several_frames(page):
            several.append(page)
    return unreadable, several


def _has_transparency(img):
    return img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info


def _page_header(page):
    """(largeur, hauteur, transparence) sans décoder l'image, pour que la toile
    soit connue sans garder toutes les pages en mémoire."""
    img = page.get("img")
    if img is not None:
        return img.width, img.height, _has_transparency(img)
    with Image.open(io.BytesIO(page["bytes"])) as header:
        return (*oriented_size(header), _has_transparency(header))


def _page_canvas(page, mode, size):
    """Image de la page centrée sur la toile. Collage sans masque : les pixels
    de l'image, alpha compris, remplacent ceux du fond, seul le fond qu'aucune
    image ne couvre est blanc."""
    try:
        own = page.get("img")
        img = own if own is not None else open_page_image(page["bytes"])
        canvas = Image.new(mode, size, "white")
        offset = ((size[0] - img.width) // 2, (size[1] - img.height) // 2)
        canvas.paste(to_display_mode(img).convert(mode), offset)
    except MemoryError:
        raise
    except Exception as e:
        raise _PageUnreadable(page) from e
    if own is None:
        img.close()
    return canvas


def frame_delay(page, delay):
    """Délai d'une image, en ms : le sien (`gif_duration`, 0 compris), sinon
    `delay`, le réglage commun."""
    own = page.get("gif_duration")
    return delay if own is None else own


def build_gif(pages, delay, loop, disposal, optimize, comment, on_step=None, should_stop=None):
    """(octets du GIF, pages écartées car illisibles). (None, écartées) si
    aucune page n'est lisible.

    delay : délai en ms des images sans délai propre (frame_delay). loop : nombre
    de répétitions, None pour un GIF sans bloc de répétition, joué une fois.

    Une seule image décodée à la fois : Pillow accepte un générateur. Chaque
    image reçoit sa propre palette, une palette commune dégraderait les
    suivantes. on_step(étape, n, total) : progression ; should_stop() vrai :
    GifCancelled."""
    left_out = []
    kept = list(pages)
    while True:
        readable = []
        for number, page in enumerate(kept, 1):
            _check(should_stop)
            _notify(on_step, STEP_READING, number, len(kept))
            try:
                if page.get("is_corrupted"):
                    raise ValueError(page.get("corruption_reason"))
                readable.append((page, _page_header(page)))
            except MemoryError:
                raise
            except Exception:
                left_out.append(page)
        if not readable:
            return None, left_out
        try:
            return _write_gif(readable, delay, loop, disposal, optimize, comment,
                              on_step, should_stop), left_out
        except _PageUnreadable as e:
            # Recommencé sans elle : la liste des délais suit la place des images
            left_out.append(e.page)
            kept = [page for page, _header in readable if page is not e.page]


def _write_gif(readable, delay, loop, disposal, optimize, comment, on_step, should_stop):
    size = (max(h[0] for _p, h in readable), max(h[1] for _p, h in readable))
    keep_alpha = any(h[2] for _p, h in readable)
    mode = "RGBA" if keep_alpha else "RGB"
    total = len(readable)
    durations = [frame_delay(page, delay) for page, _h in readable]

    def frame(number):
        _check(should_stop)
        _notify(on_step, STEP_FRAME, number + 1, total)
        return _page_canvas(readable[number][0], mode, size)

    def others():
        for number in range(1, total):
            yield frame(number)
        _notify(on_step, STEP_SAVING, total, total)

    params = {
        "format": "GIF",
        "save_all": True,
        "append_images": others(),
        "duration": durations,
        "disposal": disposal,
        # Avec optimize, Pillow rend opaques (blanches) les zones transparentes
        # des images qui suivent la première
        "optimize": optimize and not keep_alpha,
    }
    if loop is not None:
        params["loop"] = loop
    if comment:
        params["comment"] = comment.encode("utf-8")
    out = io.BytesIO()
    frame(0).save(out, **params)
    return out.getvalue()


def _check(should_stop):
    if should_stop is not None and should_stop():
        raise GifCancelled()


def _notify(on_step, step, number, total):
    if on_step is not None:
        on_step(step, number, total)
