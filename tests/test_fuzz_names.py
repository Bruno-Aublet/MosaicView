"""Fuzzing reproductible des fonctions qui transforment un nom venu d'une
archive en nom de membre de CBZ ou en nom de fichier sur le disque.

Entrées générées par un générateur pseudo-aléatoire à graine fixe (aucune
dépendance ajoutée) : assemblages de fragments piégés (« .. », séparateurs
des deux sortes, lettres de lecteur, chemins UNC, caractères de contrôle et
interdits, noms réservés de Windows, Unicode atypique, flux NTFS). Nombre
de cas borné, aucune attente : la suite reste rapide. Un échec affiche
l'entrée en cause et la graine (`SEED`) qui la reproduit.

Invariants :
- archive_names.contained_member_name : ni segment vide, « . » ou « .. », ni
  « \\ », ni caractère de contrôle, ni lettre de lecteur en tête ; idempotent.
- batch_dialogs_qt._flat_names puis _unique_flat_name (lots CBR/CB7/CBT) :
  noms non vides, jamais « . » ni « .. », sans séparateur ni caractère de
  contrôle, distincts sans tenir compte de la casse, écrits dans un vrai ZIP
  puis relus avec le contenu de chaque membre.
- utils.windows_safe_relpath puis unique_names puis safe_join (export dans
  un dossier, presse-papiers) : écrits sur le vrai disque, autant de fichiers
  que de pages, chacun sous le nom demandé (Windows n'en transforme aucun),
  tous dans le dossier.
- archive_names.tar_entry_name : sans préfixe « / » ni « ./ », idempotent,
  toujours écrivable dans un ZIP pour un nom lu par tarfile.
- temp_files.log_text : aucun caractère de contrôle (une seule ligne).
- tabs_qt._page_attr_int (attribut <Page> du ComicInfo.xml) : jamais
  d'exception, toujours un entier.
- web_import_qt.extract_images_from_js_loops (script d'une page web) :
  durée bornée, adresses http(s) distinctes, jamais plus que la limite.
- comicvine_scraper._strip_html (description ComicVine) : aucune balise,
  aucun commentaire en sortie.
- open_with_default_app_qt.openable_temp_name : aucun nom accepté avec une
  extension hors de la liste blanche, ni lu ni une fois écrit sur le disque.
"""
import io
import os
import random
import re
import time
import zipfile
from urllib.parse import urljoin

import ast_loader
from modules.qt import archive_names, temp_files
from modules.qt.comicvine_scraper import _strip_html
from modules.qt.open_with_default_app_qt import _OPENABLE_EXTS, openable_temp_name
from modules.qt.utils import safe_join, unique_names, windows_safe_relpath

SEED = 20261027

_NS = ast_loader.load("batch_dialogs_qt.py", {"_flat_names", "_unique_flat_name"}, os=os)
_flat_names, _unique_flat_name = _NS["_flat_names"], _NS["_unique_flat_name"]
_page_attr_int = ast_loader.load("tabs_qt.py", {"_page_attr_int"})["_page_attr_int"]
_WEB = ast_loader.load("web_import_qt.py", {
    "extract_images_from_js_loops", "_resolve_js_number", "_find_matching_brace",
    "_JS_CONST_NUM_RE", "_JS_CONST_STR_RE", "_JS_PAD_FN_RE", "_JS_FOR_LOOP_RE",
    "_JS_RECURSIVE_LOOP_RE", "_JS_CONCAT_TERM_RE", "_MAX_EXTRACTED_URLS", "_MAX_JS_LOOP_SPAN",
    "_MAX_JS_LOOPS", "_MAX_JS_EXPR_LENGTH", "_MAX_JS_LOOP_BODY", "_MAX_JS_EVALUATIONS"},
    _re=re, urljoin=urljoin)

ATTR_PIECES = ["0", "1", "9", "12", "-", "+", " ", "\t", "\n", "_", ".", "5", "e3", "abc", "0x1f",
               "١", "٢", "¹", "²", "Ⅻ", "①", "\x00", "inf", "nan", "9" * 5000, "﻿", "　"]
JS_PIECES = ["for (let i = ", "for (var j = ", "0", "1", "N", "5000", "4999", "1000000000000", "-3",
             "; i <= ", "; i < ", "; j < ", "; i++) {", "; j++) {", "{", "}", " el.src = ", ".src=",
             "'a.jpg'", "\"b/\"", "'x' + i + '.png'", "BASE + pad(i) + '.jpg'", "pad(i + 1)",
             "pad(N)", "pad(order[i])", " + ", "i", "j", ";", "const N = 40;", "let BASE = 'img/';",
             "var K = 7;", "function pad(n){return String(n).padStart(3,'0')}",
             "let k = 0; function step() { if (k >= N) {return;} ", "k++; setTimeout(step, 10); }",
             "//", "\n", "'", "\"", "(", ")", "'http://h/'", "'javascript:x'", "'\\\\\\\\srv\\\\x'"]
HTML_PIECES = ["<", ">", "&lt;", "&gt;", "&amp;", "&amp;lt;", "&amp;gt;", "b", "img", "IMG",
               " src=x", "/", "!", "--", "?", "&nbsp;", "<br>", "<p>", "<b>", "</b>", " ", "a",
               "1", "=", "\"", "'", "\n", "&quot;", "list of covers", "<!--", "-->", "&lt;!--"]
EXT_PIECES = [".exe", ".bat", ".cmd", ".com", ".scr", ".pif", ".lnk", ".url", ".vbs", ".js", ".hta",
              ".ps1", ".msi", ".jar", ".py", ".pyc", ".reg", ".html", ".svg", ".rtf", ".cpl",
              ".chm", ".wsf", ".library-ms", ".settingcontent-ms", ".dll",
              ".txt", ".TXT", ".pdf", ".mp4", ".xml", ".nfo", ".jpg"]
TAG_RE = re.compile(r"<(?:/?[A-Za-z]|[!?])[^<>]*>")  # balise, commentaire, <!DOCTYPE>, <?…?>

PIECES = ["..", ".", "/", "\\", "C:", "c:", "Z:", "\\\\srv\\share\\", "//srv/share/", ":", "*",
          "?", "\"", "<", ">", "|", "\n", "\r", "\t", "\x1b", "\x7f", " ", "CON", "nul", "Com1",
          "LPT9", "aux.", "a", "B", "z9", "é", "É", "ſ", "ı", "K", "‮",
          "﻿", "　", "．", "∕", "%2e%2e", "::$DATA", "~1", ".jpg", ".JPG",
          ".png", "ComicInfo.xml", "_2", " (2)", "-", "_", " ", "\U0001F600"]


def _name(rng, max_pieces=8):
    return "".join(rng.choice(PIECES) for _i in range(rng.randint(0, max_pieces)))


def _cases(count, offset):
    rng = random.Random(SEED + offset)
    for _i in range(count):
        yield rng


def _text(rng, pieces, max_pieces):
    """Assemblage de pièces ; une fois sur trois, une pièce répétée jusqu'à
    3000 fois (page ou description démesurée)."""
    parts = [rng.choice(pieces) for _i in range(rng.randint(0, max_pieces))]
    if parts and rng.random() < 0.3:
        k = rng.randrange(len(parts))
        parts[k] = parts[k] * rng.randint(1, 3000)
    return "".join(parts)


def _drive_first(name):
    return len(name) >= 2 and name[1] == ":" and name[0].isascii() and name[0].isalpha()


def test_contained_member_name_stays_in_the_extraction_folder():
    for rng in _cases(4000, 1):
        name = _name(rng)
        out = archive_names.contained_member_name(name)
        msg = f"entrée {name!a} -> {out!a} (graine {SEED})"
        if not out:
            continue
        assert "\\" not in out, msg
        assert all(s not in ("", ".", "..") for s in out.split("/")), msg
        assert not any(ord(c) < 32 for c in out), msg
        assert not _drive_first(out), msg
        assert archive_names.contained_member_name(out) == out, msg


def test_flat_names_are_writable_distinct_zip_members():
    for rng in _cases(1500, 2):
        paths = list(dict.fromkeys(p for p in (_name(rng, 6) for _i in range(rng.randint(1, 6)))
                                   if not p.endswith("/")))
        if not paths:
            continue
        flat = _flat_names(paths)
        written = set()
        outs = [_unique_flat_name(flat[p], written) for p in paths]
        msg = f"entrées {paths!a} -> {outs!a} (graine {SEED})"
        for out in outs:
            assert out not in ("", ".", ".."), msg
            assert "/" not in out and "\\" not in out, msg
            assert not any(ord(c) < 32 for c in out), msg
        assert len({o.lower() for o in outs}) == len(outs), msg
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for i, out in enumerate(outs):
                zf.writestr(out, bytes([i]))
        with zipfile.ZipFile(buf) as zf:
            assert [zf.read(info) for info in zf.infolist()] == [bytes([i]) for i in range(len(outs))], msg


def test_export_names_never_overwrite_each_other_on_disk(tmp_path):
    """Écriture réelle sur NTFS : seule preuve que Windows ne confond ni ne
    transforme aucun nom (point final, flux, casse, nom réservé)."""
    for n, rng in enumerate(_cases(150, 3)):
        names = [_name(rng, 5) for _i in range(rng.randint(1, 6))]
        rels = unique_names([windows_safe_relpath(x) for x in names])
        # Un nom de page qui est aussi un dossier d'une autre page
        # (« a » et « a/b.jpg ») est un conflit signalé par l'export : hors
        # du périmètre de ce test
        dirs = {"/".join(r.lower().split("/")[:k]) for r in rels for k in range(1, r.count("/") + 1)}
        if any(r.lower() in dirs for r in rels):
            continue
        base = tmp_path / str(n)
        base.mkdir()
        msg = f"entrées {names!a} -> {rels!a} (graine {SEED})"
        for i, rel in enumerate(rels):
            dest = safe_join(str(base), rel)
            assert dest is not None, msg
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "xb") as f:
                f.write(bytes([i]))
            assert os.path.basename(dest) in os.listdir(os.path.dirname(dest)), msg
        found = [os.path.join(root, f) for root, _d, files in os.walk(base) for f in files]
        assert len(found) == len(rels), msg


def test_tar_entry_name_has_no_prefix_and_is_writable():
    import tarfile
    for rng in _cases(3000, 4):
        name = _name(rng)
        out = archive_names.tar_entry_name(name)
        msg = f"entrée {name!a} -> {out!a} (graine {SEED})"
        assert not out.startswith(("/", "./")), msg
        assert archive_names.tar_entry_name(out) == out, msg
    # Octets quelconques dans un nom TAR (codage inconnu) : le nom lu par
    # tarfile est toujours écrivable dans un ZIP une fois normalisé
    rng = random.Random(SEED + 5)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.GNU_FORMAT, encoding="latin-1") as tf:
        for i in range(300):
            raw = bytes(rng.randrange(32, 256) for _j in range(rng.randint(1, 12)))
            info = tarfile.TarInfo(f"{i}_" + raw.decode("latin-1"))
            tf.addfile(info, io.BytesIO(b""))
    buf.seek(0)
    with tarfile.open(fileobj=buf, mode="r") as tf, zipfile.ZipFile(io.BytesIO(), "w") as zf:
        for member in tf.getmembers():
            zf.writestr(archive_names.tar_entry_name(member.name) or "_", b"")


def test_log_text_is_a_single_line():
    for rng in _cases(3000, 6):
        name = _name(rng)
        assert not any(ord(c) < 32 for c in temp_files.log_text(name)), f"{name!a} (graine {SEED})"


def test_page_attribute_sort_value_is_always_an_int():
    for rng in _cases(3000, 7):
        value = None if rng.random() < 0.05 else _text(rng, ATTR_PIECES, 6)
        assert type(_page_attr_int(value)) is int, f"{value!a} (graine {SEED})"


def test_js_loops_extraction_is_bounded():
    for rng in _cases(300, 8):
        page = _text(rng, JS_PIECES, 40)
        start = time.monotonic()
        urls = _WEB["extract_images_from_js_loops"]("https://ex.com/p/", page)
        msg = f"page {page[:300]!a}… ({len(page)} caractères, graine {SEED})"
        assert time.monotonic() - start < 3, msg
        assert len(urls) <= _WEB["_MAX_EXTRACTED_URLS"], msg
        assert len(set(urls)) == len(urls), msg
        assert all(u.startswith(("http://", "https://")) for u in urls), msg


def test_strip_html_leaves_no_tag():
    for rng in _cases(1500, 9):
        text = _text(rng, HTML_PIECES, 30)
        out = _strip_html(text)
        assert not TAG_RE.search(out), f"{text[:300]!a} -> {out[:300]!a} (graine {SEED})"


def test_open_with_default_app_never_accepts_another_extension(tmp_path):
    written = 0
    for n, rng in enumerate(_cases(1500, 10)):
        name = _text(rng, PIECES + EXT_PIECES * 3, 8)
        rel = openable_temp_name(name)
        msg = f"entrée {name[:300]!a} -> {rel!a} (graine {SEED})"
        if rel is None:
            continue
        assert os.path.splitext(rel)[1].lower() in _OPENABLE_EXTS, msg
        # Écriture réelle, comme open_file_with_default_app : Windows ne
        # transforme pas le nom (point final, flux « : »), aucun autre type
        if written >= 150:
            continue
        dest = safe_join(str(tmp_path / str(n)), rel)
        assert dest is not None, msg
        if len(dest) > 240:
            continue  # chemin trop long pour Windows : écriture refusée, rien d'ouvert
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(b"x")
        written += 1
        found = os.listdir(os.path.dirname(dest))
        assert found == [os.path.basename(dest)], msg
