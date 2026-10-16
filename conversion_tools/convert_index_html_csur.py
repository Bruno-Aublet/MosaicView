# -*- coding: utf-8 -*-
"""
Genere/synchronise les variantes CSUR (tlh-piqad, qya-tengwar, sjn-tengwar)
dans le bloc JS `const translations = {...}` d'index.html, a partir des
valeurs latines deja presentes pour tlh/qya/sjn pour un ensemble de cles
donne.

Reutilise les fonctions de conversion existantes (convert_piqad_csur.py et
convert_tengwar_csur.py) sans jamais toucher aux fichiers locales/*.json :
ces deux scripts executent leur conversion de fichier au niveau module (pas
de garde __main__), donc un import classique reecrirait locales/tlh-piqad.json
etc. On isole ici uniquement les fonctions utiles avant leur code d'execution.

Usage :
    python conversion_tools/convert_index_html_csur.py cle1 cle2 cle3 ...

Chaque cle doit deja exister (valeur latine) dans les blocs "tlh", "qya" et
"sjn" d'index.html. Le script insere/met a jour les memes cles juste apres
"btn_dl2" dans les blocs "tlh-piqad", "qya-tengwar" et "sjn-tengwar".

Piege connu : si une cle contient un mot technique/nom propre non present
dans locales/*.json (ex. "Assets", nom d'une section GitHub), l'ajouter a
PRESERVE_RE dans convert_piqad_csur.py ET convert_tengwar_csur.py avant de
lancer la conversion, sinon le mot sera partiellement transcrit en CSUR.
"""
import json
import re
import sys
import os

CONV_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(CONV_DIR)
INDEX_HTML = os.path.join(ROOT, "index.html")

ANCHOR_KEY = "btn_dl2"


def load_module_prefix(filename, stop_marker):
    path = os.path.join(CONV_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    src = src.split(stop_marker)[0]
    ns = {"__name__": "_isolated_" + filename, "__file__": path}
    exec(compile(src, path, "exec"), ns)
    return ns


_piqad_ns = load_module_prefix("convert_piqad_csur.py", "# --- Test rapide ---")
to_piqad = _piqad_ns["to_piqad"]

_tengwar_ns = load_module_prefix("convert_tengwar_csur.py", "# --- Conversion ---")
split_preserve = _tengwar_ns["split_preserve"]
transcribe_batch = _tengwar_ns["transcribe_batch"]


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


def extract_source_values(lines, lang, keys):
    start, end = get_block_range(lines, lang)
    values = {}
    for j in range(start, end):
        for key in keys:
            m = re.match(rf'\s*"{key}":\s*"((?:[^"\\]|\\.)*)",?\s*$', lines[j])
            if m:
                values[key] = json.loads('"' + m.group(1) + '"')
    missing = [k for k in keys if k not in values]
    if missing:
        raise SystemExit(f"Cles manquantes dans le bloc source {lang}: {missing}")
    return values


def remove_existing_keys(lines, lang, keys):
    start, end = get_block_range(lines, lang)
    to_delete = set()
    for j in range(start, end):
        for key in keys:
            if re.match(rf'\s*"{key}":\s*"', lines[j]):
                to_delete.add(j)
    return [l for i, l in enumerate(lines) if i not in to_delete]


def insert_block(lines, lang, values, keys):
    start, end = get_block_range(lines, lang)
    anchor_line = None
    for j in range(start, end):
        if f'"{ANCHOR_KEY}":' in lines[j]:
            anchor_line = j
            break
    if anchor_line is None:
        raise SystemExit(f"Ancre {ANCHOR_KEY!r} introuvable dans le bloc: {lang}")
    indent = "    "
    new_lines = [
        f'{indent}"{key}": {json.dumps(values[key], ensure_ascii=False)},'
        for key in keys
    ]
    lines[anchor_line] = lines[anchor_line] + "\n" + "\n".join(new_lines)
    return lines


def transcribe_dict(src, keys, mode):
    result = {}
    for key in keys:
        segments = split_preserve(src[key])
        raw_texts = [seg for seg, preserved in segments if not preserved and seg.strip()]
        transcribed = transcribe_batch(raw_texts, mode) if raw_texts else []
        it = iter(transcribed)
        parts = []
        for seg, preserved in segments:
            if preserved or not seg.strip():
                parts.append(seg)
            else:
                parts.append(next(it))
        result[key] = "".join(parts)
    return result


def main(keys):
    with open(INDEX_HTML, "r", encoding="utf-8") as f:
        lines = f.read().split("\n")

    # Repart d'un etat propre : retire d'abord toute valeur existante pour ces
    # cles dans les 3 blocs CSUR (idempotent si le script est relance).
    for lang in ("tlh-piqad", "qya-tengwar", "sjn-tengwar"):
        lines = remove_existing_keys(lines, lang, keys)

    tlh_src = extract_source_values(lines, "tlh", keys)
    tlh_piqad = {k: to_piqad(v) for k, v in tlh_src.items()}
    lines = insert_block(lines, "tlh-piqad", tlh_piqad, keys)

    qya_src = extract_source_values(lines, "qya", keys)
    sjn_src = extract_source_values(lines, "sjn", keys)

    qya_tengwar = transcribe_dict(qya_src, keys, "quenya-tengwar-classical")
    lines = insert_block(lines, "qya-tengwar", qya_tengwar, keys)

    sjn_tengwar = transcribe_dict(sjn_src, keys, "sindarin-tengwar-general_use")
    lines = insert_block(lines, "sjn-tengwar", sjn_tengwar, keys)

    with open(INDEX_HTML, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))

    print(f"OK: cles {keys} synchronisees pour tlh-piqad, qya-tengwar, sjn-tengwar dans index.html")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python convert_index_html_csur.py cle1 [cle2 ...]")
    main(sys.argv[1:])
