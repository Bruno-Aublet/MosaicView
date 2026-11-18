#!/usr/bin/env bash
# Chaîne complète pour appliquer un bloc de traductions quenya à locales/qya.json :
# contrôle lexical (index Eldamo + vocab_qya.tsv), contrôle structurel (clés, placeholders,
# retours à la ligne, guillemets), sauvegarde, application, régénération tengwar, validation.
# S'arrête au premier échec : rien n'est écrit si un contrôle échoue.
#
# Usage (depuis la racine du dépôt) :
#   bash conversion_tools/quenya/run_block.sh <bloc.json> <prefixe> [prefixe...]
# <bloc.json> : {"chemin.de.clé": "valeur quenya", ...}
# <prefixe>   : préfixe(s) de clés couverts par le bloc (ex. dialogs.batch_pdf), pour vérifier
#               qu'aucune clé ne manque ni n'est en trop.
set -e
TOOLS="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$TOOLS/../.." && pwd)"
BLOCK="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"; shift
cd "$ROOT"
# Fichiers de travail (anomalies du contrôle lexical, sauvegarde d'avant le bloc, compte rendu
# de conversion) : un dossier temporaire supprimé à la fin du script, qu'il réussisse ou non.
# Un dossier laissé par un script tué (Ctrl+C brutal, terminal fermé) est supprimé au
# lancement suivant s'il a plus de deux heures.
TEMPROOT="${TMPDIR:-/tmp}"
find "$TEMPROOT" -maxdepth 1 -type d -name 'mosaicview_qya.*' -mmin +120 -exec rm -rf {} + 2>/dev/null || true
LOGS="$(mktemp -d "$TEMPROOT/mosaicview_qya.XXXXXX")"
DONE=0
finish() {
  status=$?
  # Échec après la sauvegarde : fichiers de langue remis dans leur état d'avant le bloc
  if [ "$DONE" = 0 ] && [ -f "$LOGS/qya.json" ]; then
    cp "$LOGS/qya.json" locales/qya.json
    cp "$LOGS/qya-tengwar.json" locales/qya-tengwar.json
    echo "ECHEC : locales/qya.json et locales/qya-tengwar.json remis dans leur etat d'avant le bloc"
    if [ -s "$LOGS/last_tengwar.log" ]; then cat "$LOGS/last_tengwar.log"; fi
  fi
  rm -rf "$LOGS"
  exit $status
}
trap finish EXIT

n=$(python -I "$TOOLS/check_qya.py" "$TOOLS/eldamo_qya_index.tsv" "$TOOLS/vocab_qya.tsv" "$BLOCK" "$LOGS/last_check.txt" "$LOGS/last_decoupages.txt")
if [ "$n" != "0" ]; then
  echo "CONTROLE LEXICAL : $n anomalie(s)"; cat "$LOGS/last_check.txt"; exit 1
fi
# Mots acceptés après retrait de terminaisons : relire chaque découpage, un découpage valide
# pouvant donner un autre mot que celui voulu.
if [ -s "$LOGS/last_decoupages.txt" ]; then
  echo "DECOUPAGES A RELIRE :"; cat "$LOGS/last_decoupages.txt"
fi
python conversion_tools/klingon/check_struct.py "$BLOCK" "$@"

cp locales/qya.json "$LOGS/qya.json"
cp locales/qya-tengwar.json "$LOGS/qya-tengwar.json"

python conversion_tools/klingon/apply_block.py locales/qya.json "$BLOCK"
PYTHONIOENCODING=utf-8 python conversion_tools/convert_tengwar_csur.py > "$LOGS/last_tengwar.log" 2>&1
python -c "
import json
t = json.load(open('locales/qya.json', encoding='utf-8'))
p = json.load(open('locales/qya-tengwar.json', encoding='utf-8'))
def flat(d, pre=''):
    for k, v in d.items():
        q = f'{pre}.{k}' if pre else k
        yield from flat(v, q) if isinstance(v, dict) else [(q, v)]
t, p = dict(flat(t)), dict(flat(p))
bad = [k for k in t if t[k].count(chr(10)) != p[k].count(chr(10))]
print('JSON OK' if not bad else f'retours a la ligne differents en tengwar : {bad}')
"
DONE=1
