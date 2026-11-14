"""entries.check_announced_size : une image dont l'en-tête annonce une taille
impossible est refusée AVANT tout décodage. Pillow réservait la mémoire de
toute l'image annoncée avant de constater que les données manquaient : un
WebP forgé de 450 octets faisait réserver 827 Mo, un TIFF compressé de 162
octets 680 Mo (fuzzing, RAPPORT_AUDITS.md).

Vérifié :
- WebP (fixe ou animé) et GIF plus grands que leur format ne le permet :
  « dimensions d'image invalides » ;
- PNG ou TIFF compressé trop court pour son image : « fichier incomplet » ;
- une image valide passe toujours, y compris une page blanche minuscule une
  fois compressée (WebP sans perte, PNG, TIFF) ;
- une page piégée devient une page illisible (cadre rouge), sans décodage.
"""
import io
import struct

import pytest
from PIL import Image

from modules.qt.archive_names import IMAGE_EXTS
from modules.qt.entries import (check_announced_size, create_entry, open_checked_image,
                                open_page_image)
from modules.qt.read_errors import read_error_text


def encoded(img, fmt, **kwargs):
    buf = io.BytesIO()
    img.save(buf, format=fmt, **kwargs)
    return buf.getvalue()


def forged_webp(height=2_097_168):
    """WebP animé dont la surface (bloc VP8X, 24 bits par côté) annonce
    24 × `height` pixels, comme le cas trouvé par le fuzzing."""
    frames = [Image.new("RGB", (24, 16), c) for c in ((255, 0, 0), (0, 0, 255))]
    data = bytearray(encoded(frames[0], "WEBP", save_all=True, append_images=frames[1:]))
    assert data[12:16] == b"VP8X"
    # Bloc VP8X : largeur - 1 aux octets 24 à 26, hauteur - 1 aux octets 27 à 29
    data[27:30] = (height - 1).to_bytes(3, "little")
    return bytes(data)


# Côté des faux TIFF et PNG : 81 millions de pixels, sous la limite de
# pixels par défaut de Pillow (la suite de tests ne prend pas celle de
# MosaicView.py), qui les refuserait sinon plus tôt ou avertirait
FORGED_SIDE = 9000


def forged_tiff(compression, size=FORGED_SIDE):
    """Petit TIFF valide dont les balises largeur, hauteur et lignes par bande
    annoncent `size` × `size` pixels."""
    data = bytearray(encoded(Image.new("RGB", (16, 16), (1, 2, 3)), "TIFF",
                             compression=compression))
    ifd = int.from_bytes(data[4:8], "little")
    for i in range(int.from_bytes(data[ifd:ifd + 2], "little")):
        entry = ifd + 2 + 12 * i
        tag = int.from_bytes(data[entry:entry + 2], "little")
        kind = int.from_bytes(data[entry + 2:entry + 4], "little")
        if tag in (256, 257, 278):
            width = 2 if kind == 3 else 4
            data[entry + 8:entry + 8 + width] = size.to_bytes(width, "little")
    return bytes(data)


def forged_png(size=FORGED_SIDE):
    """Petit PNG valide dont l'en-tête IHDR annonce `size` × `size` pixels
    (somme de contrôle du bloc recalculée)."""
    import zlib
    data = bytearray(encoded(Image.new("RGB", (16, 16)), "PNG"))
    data[16:24] = struct.pack(">II", size, size)
    data[29:33] = struct.pack(">I", zlib.crc32(bytes(data[12:29])))
    return bytes(data)


# ---------------------------------------------------------------------------
# Pièges refusés avant décodage
# ---------------------------------------------------------------------------

def test_forged_animated_webp_is_refused():
    data = forged_webp()
    assert Image.open(io.BytesIO(data)).size == (24, 2_097_168)
    with pytest.raises(ValueError, match="Dimensions invalides"):
        open_checked_image(data)


@pytest.mark.parametrize("compression", ["tiff_adobe_deflate", "tiff_lzw", "packbits", "raw"])
def test_forged_compressed_tiff_is_refused(compression):
    with pytest.raises(ValueError, match="truncated"):
        open_page_image(forged_tiff(compression))


def test_forged_png_is_refused():
    with pytest.raises(ValueError, match="truncated"):
        open_page_image(forged_png())


@pytest.mark.parametrize("data, reason", [
    (forged_webp(), "messages.read_errors.invalid_dimensions"),
    (forged_tiff("tiff_adobe_deflate"), "messages.read_errors.truncated"),
])
def test_forged_page_becomes_an_unreadable_page_with_its_reason(data, reason, monkeypatch):
    from modules.qt import read_errors
    monkeypatch.setattr(read_errors, "_", lambda key, **kw: key)
    entry = create_entry("p01.webp" if data[:4] == b"RIFF" else "p01.tif", data, IMAGE_EXTS)
    assert entry["is_corrupted"]
    assert entry["bytes"] is data                        # gardée telle quelle
    assert read_error_text(entry["corruption_reason"]) == reason


# ---------------------------------------------------------------------------
# Les images valides passent toujours
# ---------------------------------------------------------------------------

def test_largest_valid_webp_passes():
    data = encoded(Image.new("RGB", (16383, 4), "white"), "WEBP", lossless=True)
    check_announced_size(Image.open(io.BytesIO(data)), len(data))


@pytest.mark.parametrize("fmt, kwargs", [
    ("WEBP", {"lossless": True}),
    ("PNG", {}),
    ("TIFF", {"compression": "tiff_adobe_deflate"}),
    ("TIFF", {"compression": "tiff_lzw"}),
    ("TIFF", {"compression": "packbits"}),
])
def test_large_blank_page_passes_however_small_its_file(fmt, kwargs):
    """Une vraie page blanche se compresse énormément (WebP sans perte :
    1,4 Ko pour un A4 à 600 dpi) : jamais refusée."""
    data = encoded(Image.new("1", (4960, 7016), 1), fmt, **kwargs) if fmt != "WEBP" else \
        encoded(Image.new("RGB", (4960, 7016), "white"), fmt, **kwargs)
    img = Image.open(io.BytesIO(data))
    check_announced_size(img, len(data))
    assert create_entry("p01." + fmt.lower(), data, IMAGE_EXTS)["is_corrupted"] is False


def test_uncompressed_page_passes():
    data = encoded(Image.new("RGB", (2480, 3508), (200, 180, 160)), "TIFF")
    check_announced_size(Image.open(io.BytesIO(data)), len(data))


def test_formats_without_a_compression_bound_are_never_refused_for_their_size():
    """JPEG, AVIF, WebP sans perte : pas de taux de compression maximal
    utilisable, donc aucun refus d'après la taille du fichier."""
    img = Image.new("RGB", (8, 8))
    img.format = "JPEG"
    img._size = (60000, 60000)
    check_announced_size(img, 100)
