#!/usr/bin/env bash
# Chaîne complète pour appliquer un bloc de traductions sindarines à locales/sjn.json :
# contrôle lexical (index Eldamo + vocab_sjn.tsv), contrôle structurel (clés, placeholders,
# retours à la ligne, guillemets), sauvegarde, application, régénération tengwar, validation.
# S'arrête au premier échec : rien n'est écrit si un contrôle échoue.
#
# Usage (depuis la racine du dépôt) :
#   bash conversion_tools/sindarin/run_block.sh <bloc.json> <prefixe> [prefixe...]
# <bloc.json> : {"chemin.de.clé": "valeur sindarine", ...}
# <prefixe>   : préfixe(s) de clés couverts par le bloc (ex. dialogs.batch_pdf), pour vérifier
#               qu'aucune clé ne manque ni n'est en trop.
set -e
TOOLS="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$TOOLS/../.." && pwd)"
BLOCK="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"; shift
cd "$ROOT"
# Sorties jetables (anomalies du contrôle lexical, compte rendu de conversion) : hors du dépôt.
LOGS="${TEMP:-/tmp}/mosaicview_sjn_logs"
mkdir -p "$LOGS"

n=$(python -I "$TOOLS/check_sjn.py" "$TOOLS/eldamo_sjn_index.tsv" "$TOOLS/vocab_sjn.tsv" "$BLOCK" "$LOGS/last_check.txt")
if [ "$n" != "0" ]; then
  echo "CONTROLE LEXICAL : $n anomalie(s)"; cat "$LOGS/last_check.txt"; exit 1
fi
python conversion_tools/klingon/check_struct.py "$BLOCK" "$@"

BACKUP="${TEMP:-/tmp}/mosaicview_sjn_backup"
mkdir -p "$BACKUP"
stamp=$(date +%Y%m%d_%H%M%S)
cp locales/sjn.json "$BACKUP/sjn.$stamp.json"
cp locales/sjn-tengwar.json "$BACKUP/sjn-tengwar.$stamp.json"
echo "Sauvegarde : $BACKUP/*.$stamp.json"

python conversion_tools/klingon/apply_block.py locales/sjn.json "$BLOCK"
PYTHONIOENCODING=utf-8 python conversion_tools/convert_tengwar_csur.py > "$LOGS/last_tengwar.log" 2>&1
python -c "
import json
t = json.load(open('locales/sjn.json', encoding='utf-8'))
p = json.load(open('locales/sjn-tengwar.json', encoding='utf-8'))
def flat(d, pre=''):
    for k, v in d.items():
        q = f'{pre}.{k}' if pre else k
        yield from flat(v, q) if isinstance(v, dict) else [(q, v)]
t, p = dict(flat(t)), dict(flat(p))
bad = [k for k in t if t[k].count(chr(10)) != p[k].count(chr(10))]
print('JSON OK' if not bad else f'retours a la ligne differents en tengwar : {bad}')
"
