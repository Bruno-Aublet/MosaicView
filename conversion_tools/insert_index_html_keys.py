# -*- coding: utf-8 -*-
"""
Moteur generique d'insertion de cles de traduction dans le bloc JS
`const translations = {...}` d'index.html (independant des fichiers
locales/*.json, qui ont leur propre workflow).

Ne contient aucune traduction : a importer depuis un script d'appel ponctuel
qui fournit les traductions (voir add_xxx_translations.py a la racine du
projet pour l'equivalent cote locales/*.json).

Pour les 3 variantes CSUR (tlh-piqad, qya-tengwar, sjn-tengwar), ne pas
traduire directement ici : inserer d'abord les valeurs latines (tlh/qya/sjn)
via insert_keys(), puis lancer conversion_tools/convert_index_html_csur.py
avec les memes cles pour generer les 3 variantes CSUR par conversion.

Usage (depuis un script d'appel) :
    from conversion_tools.insert_index_html_keys import insert_keys

    TRANSLATIONS = {
        "fr": {"ma_cle": "Valeur fr"},
        "en": {"ma_cle": "Value en"},
        ...
    }
    insert_keys(TRANSLATIONS, anchor_key="btn_dl2")
"""
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_HTML = os.path.join(ROOT, "index.html")

BLOCK_PATTERN = re.compile(r'^\s*"([a-zA-Z-]+)":\s*\{\s*$')


def find_block_starts(lines):
    starts = []
    for i, line in enumerate(lines):
        m = BLOCK_PATTERN.match(line)
        if m and m.group(1) != "lang_names":
            starts.append((i, m.group(1)))
    return starts


def get_block_range(lines, lang):
    starts = find_block_starts(lines)
    for idx, (line_no, l) in enumerate(starts):
        if l == lang:
            end_line = starts[idx + 1][0] if idx + 1 < len(starts) else len(lines)
            return line_no, end_line
    raise SystemExit(f"Bloc introuvable dans index.html: {lang}")


def remove_existing_keys(lines, lang, keys):
    start, end = get_block_range(lines, lang)
    to_delete = set()
    for j in range(start, end):
        for key in keys:
            if re.match(rf'\s*"{key}":\s*"', lines[j]):
                to_delete.add(j)
    return [l for i, l in enumerate(lines) if i not in to_delete]


def insert_block(lines, lang, values, anchor_key):
    start, end = get_block_range(lines, lang)
    anchor_line = None
    for j in range(start, end):
        if f'"{anchor_key}":' in lines[j]:
            anchor_line = j
            break
    if anchor_line is None:
        raise SystemExit(f"Ancre {anchor_key!r} introuvable dans le bloc: {lang}")
    indent = "    "
    new_lines = [
        f'{indent}"{key}": {json.dumps(value, ensure_ascii=False)},'
        for key, value in values.items()
    ]
    lines[anchor_line] = lines[anchor_line] + "\n" + "\n".join(new_lines)
    return lines


def insert_keys(translations, anchor_key):
    """
    translations : dict {code_langue: {cle: valeur}}.
    Toutes les langues presentes dans translations doivent exister comme bloc
    dans index.html. Idempotent : si les cles existent deja dans un bloc,
    elles sont retirees puis reinserees (permet de relancer sans doublon).
    """
    with open(INDEX_HTML, "r", encoding="utf-8") as f:
        lines = f.read().split("\n")

    for lang, values in translations.items():
        keys = list(values.keys())
        lines = remove_existing_keys(lines, lang, keys)
        lines = insert_block(lines, lang, values, anchor_key)

    with open(INDEX_HTML, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))

    print(f"OK: {len(translations)} langue(s) mises a jour dans index.html")
