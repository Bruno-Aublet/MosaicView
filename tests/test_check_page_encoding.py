"""
test_check_page_encoding.py — Garde-fou : toute page réécrite s'encode par
l'encodeur commun (entries.encode_page, entries.encode_frames pour une image
à plusieurs images), jamais par un `img.save(...)` local.

Chaque encodeur qui réécrivait une page à sa façon appliquait ses propres
règles, et en oubliait : profil de couleur, résolution, métadonnées,
transparence, « sans perte » perdus selon le chemin (RAPPORT_AUDITS.md,
Audit 5, P4-1). Les règles ne sont écrites qu'une fois, dans encode_page ;
chaque appelant ne fixe que ce qui lui est propre (format, qualité…).

Scan AST de modules/qt/*.py : tout appel `.save(...)` d'une image Pillow
(reconnu à son format, passé en `format=` ou en deuxième argument, ou à
`save_all`) hors des fonctions de COMMON_ENCODERS doit figurer dans
ALLOWED_SAVES, avec la raison pour laquelle il ne réécrit pas une page du
comic (vignette, aperçu, fichier hors du comic, création d'une page neuve à
partir d'une autre source). Une nouvelle écriture de page passe par
encode_page (skill apply-image-operation).

Fait partie de la suite pytest normale :  python -m pytest tests/
"""
import ast
import os

import ast_loader

QT_DIR = str(ast_loader.QT_DIR)

COMMON_ENCODERS = {
    ("entries.py", "encode_page"),
    ("entries.py", "encode_frames"),
}

# (fichier, fonction qualifiée) -> raison
ALLOWED_SAVES = {
    ("entries.py", "_jpeg_quality_tables"):
        "tables de référence de l'estimation de la qualité JPEG, jamais écrites dans le comic",
    ("clipboard_qt.py", "paste_from_system_clipboard"):
        "image collée depuis le presse-papiers de Windows (bitmap sans format) : page neuve, "
        "pas une page réécrite",
    ("comicvine_dialog_qt.py", "_bytes_to_pixmap"):
        "couverture ComicVine à afficher",
    ("gif_writer.py", "_write_gif"):
        "création d'un GIF animé à partir de plusieurs pages (choix de l'utilisateur : "
        "reste à part, ne réécrit pas une page)",
    ("ico_creator_qt.py", "IcoCreatorDialog._on_validate_final"):
        "création d'une icône neuve, toujours en RGBA et dans toutes les tailles standard",
    ("icon_toolbar_qt.py", "_pil_to_qpixmap"):
        "icône de la barre à afficher",
    ("image_processing_qt.py", "apply_adjustments"):
        "aperçu d'une image 1 bit converti pour l'affichage (for_preview), jamais écrit",
    ("paste_image_tool_qt.py", "PasteImageViewerMixin.perform_paste_image"):
        "copie de travail d'une image collée (historique, macros), pas une page",
    ("paste_image_tool_qt.py", "PasteImageViewerMixin._save_paste_image_for_current_page"):
        "copie de travail d'une image collée, gardée par page de la visionneuse",
    ("pdf_loading_qt.py", "_pdf_persistent_process._save_page_jpeg"):
        "page neuve rendue depuis un PDF, dans le process PDF séparé",
    ("pdf_loading_qt.py", "_pdf_persistent_process"):
        "page neuve rendue depuis un PDF, dans le process PDF séparé",
    ("pdf_page_images.py", "_with_icc_profile"):
        "image brute extraite d'un PDF : page neuve",
    ("pdf_page_images.py", "_oriented_png"):
        "image brute extraite d'un PDF : page neuve",
    ("pdf_page_images.py", "_stored_image"):
        "image brute extraite d'un PDF : page neuve",
    ("pdf_page_images.py", "stitch_vertically"):
        "images d'un PDF recollées : page neuve",
    ("printing_qt.py", "_open_print_dialog"):
        "fichier temporaire d'impression, hors du comic",
    ("printing_qt.py", "_PrintWorker.run"):
        "fichier temporaire d'impression, hors du comic",
    ("renumbering_qt.py", "_FirstPageDialog.__init__"):
        "vignette à afficher",
    ("resize_dialog_qt.py", "OutlierDialog._build_page_widgets"):
        "vignette à afficher",
    ("scan_wia.py", "scan_image"):
        "page neuve numérisée",
    ("thumbnail_export_qt.py", "write_thumbnail"):
        "vignette exportée dans un dossier, hors du comic",
}


def _is_image_save(call):
    """Appel `.save(...)` d'une image Pillow : format en `format=` ou en
    deuxième argument, `save_all`, ou options passées en `**dict` (le format
    peut y être). Exclut painter.save() (Qt, sans argument) et les
    enregistrements d'autres objets (classeur Excel, document PDF), qui ne
    donnent pas de format d'image."""
    if not (isinstance(call.func, ast.Attribute) and call.func.attr == "save"):
        return False
    keywords = {k.arg for k in call.keywords}
    return ("format" in keywords or "save_all" in keywords or None in keywords
            or len(call.args) >= 2)


def _image_saves(tree):
    """[(fonction qualifiée, ligne)] des enregistrements d'image du module."""
    found = []

    def walk(node, qual):
        for child in ast.iter_child_nodes(node):
            q = qual
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                q = qual + [child.name]
            if isinstance(child, ast.Call) and _is_image_save(child):
                found.append((".".join(q) or "<module>", child.lineno))
            walk(child, q)

    walk(tree, [])
    return found


def _all_saves():
    out = []
    for name in sorted(os.listdir(QT_DIR)):
        if name.endswith(".py"):
            tree = ast_loader.module_tree(os.path.join(QT_DIR, name))
            out += [(name, qual, line) for qual, line in _image_saves(tree)]
    return out


SAVES = _all_saves()


def test_pages_are_encoded_only_by_the_common_encoder():
    bad = [f"  {f}:{line} ({q})" for f, q, line in SAVES
           if (f, q) not in COMMON_ENCODERS and (f, q) not in ALLOWED_SAVES]
    assert not bad, ("Image enregistrée hors de l'encodeur commun : une page s'encode par "
                     "entries.encode_page (ou encode_frames), sinon déclarer l'exception dans "
                     "ALLOWED_SAVES avec sa raison :\n" + "\n".join(bad))


def test_exceptions_are_not_stale():
    used = {(f, q) for f, q, _line in SAVES}
    stale = [k for k in list(ALLOWED_SAVES) + list(COMMON_ENCODERS) if k not in used]
    assert not stale, f"Exceptions sans enregistrement correspondant : {stale}"


def test_scan_sees_the_common_encoders():
    assert {(f, q) for f, q, _line in SAVES} >= COMMON_ENCODERS


def test_rule_detects_a_local_page_save_and_ignores_other_saves():
    def saves(src):
        return [q for q, _line in _image_saves(ast.parse(src))]
    assert saves("def f(img, buf):\n    img.save(buf, format='PNG')\n") == ["f"]
    assert saves("def f(img, buf):\n    img.save(buf, 'JPEG', quality=90)\n") == ["f"]
    assert saves("def f(frames, buf):\n    frames[0].save(buf, save_all=True)\n") == ["f"]
    assert saves("def f(img, buf, params):\n    img.save(buf, **params)\n") == ["f"]
    assert saves("def f(painter):\n    painter.save()\n") == []
    assert saves("def f(wb, path):\n    wb.save(path)\n") == []
    assert saves("def f(doc, path):\n    doc.save(path, encryption=0)\n") == []
