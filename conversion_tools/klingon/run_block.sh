#!/usr/bin/env bash
# Chaîne complète pour appliquer un bloc de traductions klingonnes à locales/tlh.json :
# contrôle lexical (dictionnaire boQwI'), contrôle structurel (clés, placeholders, retours
# à la ligne, guillemets), sauvegarde, application, régénération pIqaD, validation JSON.
# S'arrête au premier échec : rien n'est écrit si un contrôle échoue.
#
# Usage (depuis la racine du dépôt) :
#   bash conversion_tools/klingon/run_block.sh <bloc.json> <prefixe> [prefixe...]
# <bloc.json> : {"chemin.de.clé": "valeur klingon", ...}
# <prefixe>   : préfixe(s) de clés couverts par le bloc (ex. dialogs.batch_pdf), pour vérifier
#               qu'aucune clé ne manque ni n'est en trop.
set -e
TOOLS="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$TOOLS/../.." && pwd)"
BLOCK="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"; shift
cd "$ROOT"
# Sorties jetables (mots non reconnus, compte rendu de conversion) : hors du dépôt.
LOGS="${TEMP:-/tmp}/mosaicview_tlh_logs"
mkdir -p "$LOGS"

n=$(python -I "$TOOLS/check_tlh.py" "$TOOLS/boqwi_index.tsv" "$BLOCK" "$LOGS/last_check.txt")
if [ "$n" != "0" ]; then
  echo "CONTROLE LEXICAL : $n mot(s) non reconnu(s)"; cat "$LOGS/last_check.txt"; exit 1
fi
python "$TOOLS/check_struct.py" "$BLOCK" "$@"

BACKUP="${TEMP:-/tmp}/mosaicview_tlh_backup"
mkdir -p "$BACKUP"
stamp=$(date +%Y%m%d_%H%M%S)
cp locales/tlh.json "$BACKUP/tlh.$stamp.json"
cp locales/tlh-piqad.json "$BACKUP/tlh-piqad.$stamp.json"
echo "Sauvegarde : $BACKUP/*.$stamp.json"

python "$TOOLS/apply_block.py" locales/tlh.json "$BLOCK"
PYTHONIOENCODING=utf-8 python conversion_tools/convert_piqad_csur.py > "$LOGS/last_piqad.log" 2>&1
python -c "
import json
t = json.load(open('locales/tlh.json', encoding='utf-8'))
p = json.load(open('locales/tlh-piqad.json', encoding='utf-8'))
def flat(d, pre=''):
    for k, v in d.items():
        q = f'{pre}.{k}' if pre else k
        yield from flat(v, q) if isinstance(v, dict) else [(q, v)]
t, p = dict(flat(t)), dict(flat(p))
bad = [k for k in t if t[k].count(chr(10)) != p[k].count(chr(10))]
print('JSON OK' if not bad else f'retours a la ligne differents en pIqaD : {bad}')
"
