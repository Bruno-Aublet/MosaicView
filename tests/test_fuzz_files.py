"""Fichiers abîmés ou piégés passés par les vrais lecteurs : images
(entries.create_entry), CBZ/EPUB et CBT (lecteurs d'archive_loader).

Régressions :
- detect_archive_type lit le membre « mimetype » d'un ZIP sans jamais le
  décompresser en entier (bombe de décompression : quelques Ko qui
  deviennent plusieurs Go, à chaque ouverture et dans chaque lot) ;
- epub_reading_order refuse un document de l'EPUB plus grand que
  MAX_MEMBER_SIZE (l'ordre des noms prend le relais) ;
- une résolution NaN ou infinie dans l'en-tête d'une image vaut absence de
  résolution (l'enregistrement de la page levait ValueError) ;
- un GIF plus grand que son format ne peut le décrire est une page
  illisible (affiché, il ne pouvait plus être retouché) ;
- la réécriture d'un ComicInfo.xml garde tout son contenu et se relit
  toujours (test_comic_info.py : cas qui donnaient un fichier illisible) ;
- une archive abîmée signalée par zipfile au moyen d'une exception de
  Python (zlib.error, LZMAError, NotImplementedError, RuntimeError,
  UnicodeDecodeError) est une erreur attendue, jamais écrite dans le journal
  des erreurs ; un appel incorrect du programme (TypeError, KeyError) l'est
  toujours.

Fuzzing reproductible, à graine fixe (`SEED`) : images et archives de
synthèse abîmées par des mutations aveugles. Une image dont l'en-tête annonce
plus de 4 millions de pixels est écartée avant tout décodage : la suite ne
réserve jamais de grosse mémoire. La campagne longue, à graine aléatoire et
sous limite de mémoire, est fuzz/fuzz_files.py.
"""
import io
import math
import os
import random
import tarfile
import tracemalloc
import zipfile

import pytest
from PIL import Image

import ast_loader
from modules.qt import archive_names, entries, error_log
from modules.qt.archive_type_detector import detect_archive_type

SEED = 20261028
EXTS = archive_names.IMAGE_EXTS

# Avertissements de Pillow sur les images volontairement abîmées (EXIF
# tronqué, balise TIFF hors du fichier, taille d'image annoncée énorme) :
# attendus, ils ne signalent aucun défaut
pytestmark = [pytest.mark.filterwarnings("ignore::UserWarning"),
              pytest.mark.filterwarnings("ignore::PIL.Image.DecompressionBombWarning")]


# ---------------------------------------------------------------------------
# Membres annexes d'un ZIP : lecture bornée
# ---------------------------------------------------------------------------

def _zip_with_big_member(path, name, size, before=()):
    chunk = b"\0" * (1024 * 1024)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
        for member, data in before:
            zf.writestr(member, data)
        with zf.open(name, "w") as out:
            for _i in range(size // len(chunk)):
                out.write(chunk)
        zf.writestr("001.jpg", b"x")


def test_detect_archive_type_never_decompresses_a_whole_mimetype(tmp_path):
    """« mimetype » de 64 Mo (64 Ko dans le fichier) : lu sur quelques
    octets. Il était décompressé en entier en mémoire."""
    path = tmp_path / "a.cbz"
    _zip_with_big_member(path, "mimetype", 64 * 1024 * 1024)
    tracemalloc.start()
    try:
        assert detect_archive_type(str(path)) == "CBZ"
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 8 * 1024 * 1024, f"pic de {peak // 1024} Ko"


def test_detect_archive_type_still_recognises_an_epub(tmp_path):
    path = tmp_path / "a.epub"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("001.jpg", b"x")
    assert detect_archive_type(str(path)) == "EPUB"


def _epub(path, padding=0):
    """EPUB dont l'ordre du livre (b puis a) n'est pas celui des noms."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml",
                    '<container><rootfile full-path="c.opf"/></container>' + " " * padding)
        zf.writestr("c.opf", '<package><manifest><item id="d" href="t.xhtml"/></manifest>'
                             '<spine><itemref idref="d"/></spine></package>')
        zf.writestr("t.xhtml", '<html><img src="b.jpg"/><img src="a.jpg"/></html>')
        zf.writestr("a.jpg", b"x")
        zf.writestr("b.jpg", b"y")


def test_epub_reading_order_refuses_an_oversized_document(tmp_path, monkeypatch):
    path = tmp_path / "a.epub"
    _epub(path, padding=500)
    with zipfile.ZipFile(path) as zf:
        assert archive_names.epub_reading_order(zf, ["a.jpg", "b.jpg"]) == ["b.jpg", "a.jpg"]
        monkeypatch.setattr(archive_names, "MAX_MEMBER_SIZE", 400)
        read = []
        original_read = zf.read
        monkeypatch.setattr(zf, "read", lambda info, *a: read.append(info.filename) or original_read(info, *a))
        assert archive_names.epub_reading_order(zf, ["a.jpg", "b.jpg"]) == ["a.jpg", "b.jpg"]
    assert "META-INF/container.xml" not in read


# ---------------------------------------------------------------------------
# Résolution NaN ou infinie
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("dpi", [(math.nan, math.nan), (math.inf, 300), (300, -math.inf), math.nan])
def test_nan_or_infinite_resolution_means_no_resolution(dpi):
    img = Image.new("RGB", (4, 4))
    img.info["dpi"] = dpi
    assert entries.image_dpi(img) is None


def test_page_with_a_nan_resolution_can_be_saved():
    buf = io.BytesIO()
    Image.new("RGB", (8, 6), (10, 20, 30)).save(buf, "PNG")
    entry = entries.create_entry("p.png", buf.getvalue(), EXTS)
    img = entries.ensure_image_loaded(entry)
    img.info["dpi"] = (math.nan, math.nan)
    out = entries.save_image_to_bytes(entry)
    assert not entries.create_entry("p.png", out, EXTS)["is_corrupted"]


# ---------------------------------------------------------------------------
# GIF plus grand que son format ne peut le décrire
# ---------------------------------------------------------------------------

def _gif(left=0):
    buf = io.BytesIO()
    Image.new("P", (24, 16)).save(buf, "GIF")
    data = bytearray(buf.getvalue())
    pos = data.index(b"\x2c")  # descripteur de l'image : position x sur 2 octets
    data[pos + 1:pos + 3] = left.to_bytes(2, "little")
    return bytes(data)


def test_gif_wider_than_its_format_is_unreadable():
    """Image incluse placée à x = 65 535 : Pillow annonce 65 559 pixels de
    large, au-delà de ce qu'un GIF peut décrire. Elle était affichée, puis
    toute retouche échouait (réenregistrement impossible en GIF)."""
    entry = entries.create_entry("p.gif", _gif(left=65535), EXTS)
    assert entry["is_corrupted"]
    assert entry["corruption_reason"] == "Dimensions invalides: 65559x16"
    assert not entries.create_entry("p.gif", _gif(), EXTS)["is_corrupted"]


# ---------------------------------------------------------------------------
# Erreurs de zipfile : attendues ; appels incorrects : non
# ---------------------------------------------------------------------------

def _zip(member, data, method=zipfile.ZIP_STORED):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(member, data, compress_type=method)
    return bytearray(buf.getvalue())


def _patch(b, signature, offset, value, width=2):
    pos = bytes(b).index(signature)
    old = int.from_bytes(b[pos + offset:pos + offset + width], "little")
    b[pos + offset:pos + offset + width] = value(old).to_bytes(width, "little")
    return b


def _unknown_method():
    return _patch(_zip("a.jpg", b"abc"), b"PK\x01\x02", 10, lambda _v: 98)


def _encrypted():
    return _patch(_zip("a.jpg", b"abc"), b"PK\x01\x02", 8, lambda v: v | 1)


def _bad_deflate():
    b = _zip("a.jpg", bytes(range(256)) * 8, zipfile.ZIP_DEFLATED)
    pos = bytes(b).index(b"PK\x03\x04") + 30 + len("a.jpg")
    b[pos] = 0xFF  # bloc de type réservé : « invalid block type »
    return b


def _bad_lzma():
    b = _zip("a.jpg", bytes(range(256)) * 8, zipfile.ZIP_LZMA)
    pos = bytes(b).index(b"PK\x03\x04") + 30 + len("a.jpg") + 9
    b[pos:pos + 16] = b"\xff" * 16
    return b


def _bad_local_name():
    b = _patch(_zip("ab.jpg", b"abc"), b"PK\x03\x04", 6, lambda v: v | 0x800)
    b[bytes(b).index(b"PK\x03\x04") + 30 + 1] = 0xB3
    return b


@pytest.mark.parametrize("make, kind", [
    (_unknown_method, NotImplementedError), (_encrypted, RuntimeError), (_bad_deflate, Exception),
    (_bad_lzma, Exception), (_bad_local_name, UnicodeDecodeError)])
def test_damaged_zip_errors_are_expected(make, kind):
    with zipfile.ZipFile(io.BytesIO(bytes(make()))) as zf:
        with pytest.raises(kind) as raised:
            zf.read(zf.infolist()[0])
    assert not isinstance(raised.value, (OSError, zipfile.BadZipFile)), "cas déjà reconnu par sa classe"
    assert error_log.is_expected_error(raised.value)


def test_damaged_compressed_cbt_errors_are_expected():
    """CBT compressé (gzip) abîmé : la décompression, faite par gzip pour
    tarfile, lève zlib.error depuis le module gzip ; elle était écrite dans
    le journal des erreurs à la lecture de la page."""
    import gzip
    import zlib
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for k in range(3):
            data = bytes((i * 7919 + k) % 251 for i in range(20000))
            info = tarfile.TarInfo(f"00{k}.jpg")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    damaged = bytearray(gzip.compress(buf.getvalue(), mtime=0))
    damaged[410] ^= 0xFF
    with pytest.raises(zlib.error) as raised:
        with tarfile.open(fileobj=io.BytesIO(bytes(damaged)), mode="r:*") as tf:
            for member in tf.getmembers():
                tf.extractfile(member).read()
    assert error_log.is_expected_error(raised.value)


def test_program_errors_raised_inside_zipfile_are_still_unexpected():
    with zipfile.ZipFile(io.BytesIO(bytes(_zip("a.jpg", b"abc")))) as zf:
        with pytest.raises(KeyError) as missing:
            zf.getinfo("absent.jpg")
        with pytest.raises(TypeError) as wrong:
            zf.setpassword("texte")  # bytes attendu
    assert not error_log.is_expected_error(missing.value)
    assert not error_log.is_expected_error(wrong.value)


# ---------------------------------------------------------------------------
# Fuzzing reproductible
# ---------------------------------------------------------------------------

def _image_seeds():
    base = Image.new("RGB", (24, 16), (200, 30, 30))
    seeds = []
    for ext, img, fmt, kw in [
            (".jpg", base, "JPEG", {}), (".jpg", base.convert("CMYK"), "JPEG", {}),
            (".png", base.convert("RGBA"), "PNG", {}), (".png", base.convert("P"), "PNG", {"transparency": 0}),
            (".png", base.convert("I;16"), "PNG", {}), (".gif", base, "GIF", {}),
            (".webp", base, "WEBP", {}), (".tif", base, "TIFF", {"compression": "tiff_lzw", "dpi": (300, 300)}),
            (".tif", base.convert("1"), "TIFF", {"compression": "group4"}), (".bmp", base, "BMP", {}),
            (".ico", base.resize((32, 32)), "ICO", {})]:
        buf = io.BytesIO()
        img.save(buf, fmt, **kw)
        seeds.append((ext, buf.getvalue()))
    return seeds


_LIMITS = [0, 1, 0x7F, 0x80, 0xFF, 0x7FFF, 0xFFFF, 0x7FFFFFFF, 0xFFFFFFFF]


def _mutate(rng, data, keep=0):
    b = bytearray(data)
    for _i in range(rng.choice((1, 1, 2, 4))):
        if len(b) <= keep + 4:
            break
        pos = rng.randrange(keep, len(b) - 4)
        op = rng.randrange(6)
        if op == 0:
            b[pos] ^= 1 << rng.randrange(8)
        elif op == 1:
            b[pos] = rng.choice((0, 0xFF, 0x80, rng.randrange(256)))
        elif op == 2:
            width = rng.choice((2, 4))
            value = rng.choice(_LIMITS) & ((1 << (8 * width)) - 1)
            b[pos:pos + width] = value.to_bytes(width, rng.choice(("little", "big")))
        elif op == 3:
            del b[rng.randrange(max(keep + 1, len(b) // 4), len(b)):]
        elif op == 4:
            b[pos:pos] = bytes(rng.randrange(256) for _j in range(rng.randint(1, 32)))
        else:
            del b[pos:pos + rng.randint(1, 32)]
    return bytes(b)


def _small_enough(data):
    """En-tête lisible et au plus 4 millions de pixels, ou illisible (rien à
    réserver) : sinon l'entrée est écartée avant tout décodage."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            return img.size[0] * img.size[1] <= 4_000_000
    except Exception:  # noqa: BLE001
        return True


def test_damaged_images_never_break_page_creation():
    """create_entry ne lève jamais ; une page acceptée s'affiche et se
    réenregistre en une image lisible."""
    seeds = _image_seeds()
    rng = random.Random(SEED)
    tried = 0
    for _i in range(500):
        ext, data = rng.choice(seeds)
        data = _mutate(rng, data)
        if not _small_enough(data):
            continue
        tried += 1
        msg = f"graine {SEED}, entrée {_i}, {ext}"
        entry = entries.create_entry("Aaaa/p" + ext, data, EXTS)
        if entry["is_corrupted"]:
            continue
        assert entry["img_width"] > 0 and entry["img_height"] > 0, msg
        assert entries.ensure_image_loaded(entry) is not None, msg
        if entry.get("is_animated_gif") or entry.get("is_animated_image") or entry.get("is_multipage_tiff"):
            continue
        out = entries.save_image_to_bytes(entry)
        assert not entries.create_entry(entry["orig_name"], out, EXTS)["is_corrupted"], msg
    assert tried > 400


def _readers():
    return ast_loader.load(
        "archive_loader.py",
        {"_zip_member_reader", "_check_member_size", "AlteredMemberError", "create_page_entry",
         "_tar_member_reader", "_tar_info_read"},
        os=os, zipfile=zipfile, MAX_MEMBER_SIZE=archive_names.MAX_MEMBER_SIZE,
        create_entry=entries.create_entry, IMAGE_EXTS=EXTS)


@pytest.fixture
def logged(monkeypatch):
    """Erreurs que create_page_entry enverrait au journal des erreurs."""
    unexpected = []
    monkeypatch.setattr(error_log, "log_if_unexpected",
                        lambda exc, *a, **k: unexpected.append(exc) if not error_log.is_expected_error(exc)
                        else None)
    return unexpected


def _zip_seed(rng, seeds):
    buf = io.BytesIO()
    methods = (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA)
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("ComicInfo.xml", "<ComicInfo/>", compress_type=rng.choice(methods))
        for name in ("001.jpg", "Bbbb/002.png", "003.gif"):
            zf.writestr(name, rng.choice(seeds)[1], compress_type=rng.choice(methods))
    b = bytearray(buf.getvalue())
    # Champs d'en-tête : méthode, drapeaux (chiffrement, UTF-8), tailles
    for _i in range(rng.randint(0, 2)):
        spots = [i for i in range(len(b)) if b[i:i + 4] in (b"PK\x01\x02", b"PK\x03\x04")]
        pos = rng.choice(spots)
        off = rng.choice((6, 8, 10, 18, 20, 22, 24))
        value = rng.choice((0, 1, 8, 12, 14, 98, 0x0001, 0x0800, 0xFFFF))
        b[pos + off:pos + off + 2] = value.to_bytes(2, "little")
    return bytes(b)


def test_damaged_cbz_pages_are_read_like_the_loader(tmp_path, logged):
    """La lecture de LoadWorker.load_archive : detect_archive_type, liste
    des noms, puis chaque page par create_page_entry, qui ne lève jamais ;
    jamais plus d'octets que la taille annoncée ; aucune erreur d'une
    archive abîmée envoyée au journal des erreurs."""
    ns = _readers()
    seeds = _image_seeds()
    rng = random.Random(SEED + 1)
    path = tmp_path / "a.cbz"
    for i in range(300):
        data = _zip_seed(rng, seeds)
        path.write_bytes(_mutate(rng, data, keep=4) if rng.random() < 0.6 else data)
        msg = f"graine {SEED}, entrée {i}"
        assert detect_archive_type(str(path)) in ("CBZ", None), msg
        try:
            with archive_names.open_zip(str(path)) as archive:
                names = sorted(archive.namelist())
                read_member = ns["_zip_member_reader"](archive)
                for name in names:
                    if not _small_enough_member(archive, name):
                        continue
                    entry, _message = ns["create_page_entry"](name, lambda n=name: read_member(n))
                    info = read_member.last_info
                    if entry["bytes"] is not None and info is not None:
                        assert len(entry["bytes"]) <= info.file_size, msg
        except Exception as e:  # noqa: BLE001
            # Archive refusée en entier (message « archive invalide ») : le
            # chargeur n'écrit au journal qu'une erreur inattendue
            assert error_log.is_expected_error(e), f"{msg} : {e!r}"
        assert not logged, f"{msg} : {logged!r}"


def _small_enough_member(archive, name):
    try:
        info = archive.getinfo(name)
        return info.file_size < 4 * 1024 * 1024
    except KeyError:
        return True


_XML_TEXTS = ["Aaaa", "", "  ", "é à", "\U0001F600", "a&amp;b", "&lt;x&gt;", "&#233;", "<![CDATA[<b>&]]>",
              "ligne\nligne", "x]]&gt;y"]
_XML_ROOT_ATTRS = ['xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"', "note='a>b'", 'autre="&lt;&amp;&quot;"',
                   'saut="x&#10;y"', 'uni="é"', 'xmlns="http://aaaa.example/ci"']


def _xml_document(rng):
    """ComicInfo.xml de synthèse : champs, balise inconnue à attributs et
    sous-balise, <Pages>, racine éventuellement vide (auto-fermante),
    commentaire trompeur avant la racine, codage variable."""
    children = []
    for _i in range(rng.randint(0, 5)):
        tag = rng.choice(("Title", "Series", "Summary", "Notes", "Inconnu"))
        attrs = rng.choice(("", ' a="1"', ' b="&lt;2&gt;"'))
        if tag == "Inconnu" and rng.random() < 0.5:
            children.append(f"<{tag}{attrs}><Sous c='é'>{rng.choice(_XML_TEXTS)}</Sous></{tag}>")
        else:
            children.append(f"<{tag}{attrs}>{rng.choice(_XML_TEXTS)}</{tag}>")
    if rng.random() < 0.4:
        children.append('<Pages><Page Image="0" Type="FrontCover"/><Page Image="1" Bookmark="é&amp;"/></Pages>')
    attrs = "".join(" " + a for a in rng.sample(_XML_ROOT_ATTRS, rng.randint(0, 3)))
    root = f"<ComicInfo{attrs}/>" if not children and rng.random() < 0.5 else \
        f"<ComicInfo{attrs}>{rng.choice(('', chr(10)))}{''.join(children)}</ComicInfo>"
    if rng.random() < 0.3:
        root = "<!-- <ComicInfo ancien='1'> -->" + root
    encoding = rng.choice(("utf-8", "utf-16", "iso-8859-1"))
    try:
        return f'<?xml version="1.0" encoding="{encoding}"?>{root}'.encode(encoding)
    except UnicodeEncodeError:
        return f'<?xml version="1.0" encoding="utf-8"?>{root}'.encode("utf-8")


def _xml_content(root):
    """Ce que la réécriture garde : attributs de la racine, puis chaque
    balise de premier niveau avec ses attributs, son texte et ses
    sous-balises (fins de ligne normalisées, comme le fait tout lecteur ;
    texte sans les blancs d'indentation pour une balise à sous-balises)."""
    def leaf(elem):
        children = [c for c in elem if isinstance(c.tag, str)]
        text = (elem.text or "").replace("\r", "\n")
        return (elem.tag, sorted(elem.attrib.items()), text.strip() if children else text,
                [leaf(c) for c in children])
    return (sorted(root.attrib.items()),
            [(c.tag, sorted(c.attrib.items()), [sorted(p.attrib.items()) for p in c]) if c.tag == "Pages"
             else leaf(c) for c in root if isinstance(c.tag, str)])


def test_rewritten_comicinfo_keeps_everything_and_stays_readable():
    """Réécriture de ComicInfo.xml (_serialize_comic_xml, appelée par
    l'éditeur, l'import ComicVine et la synchronisation de <Pages>) : ne lève
    jamais, se relit toujours, garde chaque attribut, balise et texte ; la
    lecture des métadonnées ne lève jamais, même abîmé."""
    from modules.qt import comic_info
    rng = random.Random(SEED + 3)
    for i in range(800):
        data = _xml_document(rng)
        msg = f"graine {SEED}, entrée {i} : {data!a}"
        if rng.random() < 0.2:
            data = _mutate(rng, data)
            comic_info.parse_comic_info_xml(data)
            continue
        root = comic_info._comic_fromstring(data)
        out = comic_info._serialize_comic_xml(root, data)
        back = comic_info._comic_fromstring(out)
        assert _xml_content(back) == _xml_content(root), msg
        assert comic_info.parse_comic_info_xml(out) is not None, msg


def test_damaged_cbt_pages_are_read_like_the_loader(tmp_path, logged):
    ns = _readers()
    seeds = _image_seeds()
    rng = random.Random(SEED + 2)
    path = tmp_path / "a.cbt"
    for i in range(300):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w", format=tarfile.GNU_FORMAT) as tf:
            for name in ("./001.jpg", "Aaaa/002.png", "ComicInfo.xml"):
                content = rng.choice(seeds)[1]
                info = tarfile.TarInfo(name)
                info.size = len(content)
                tf.addfile(info, io.BytesIO(content))
        path.write_bytes(_mutate(rng, buf.getvalue()))
        msg = f"graine {SEED}, entrée {i}"
        if detect_archive_type(str(path)) != "CBT":
            continue
        try:
            with tarfile.open(str(path), "r:*") as archive:
                members = [m for m in archive.getmembers() if m.isfile()]
                read_member = ns["_tar_member_reader"](archive)
                for member in members:
                    if member.size > 4 * 1024 * 1024:
                        continue
                    try:
                        page = read_member(member.name)
                    except Exception as e:  # noqa: BLE001
                        assert error_log.is_expected_error(e), f"{msg} : {e!r}"
                        continue
                    assert len(page) <= member.size, msg
                    entries.create_entry(archive_names.tar_entry_name(member.name), page, EXTS)
        except Exception as e:  # noqa: BLE001
            assert error_log.is_expected_error(e), f"{msg} : {e!r}"
        assert not logged, f"{msg} : {logged!r}"
