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
# Fichiers de travail (mots non reconnus, sauvegarde d'avant le bloc, compte rendu de
# conversion) : un dossier temporaire supprimé à la fin du script, qu'il réussisse ou non.
# Un dossier laissé par un script tué (Ctrl+C brutal, terminal fermé) est supprimé au
# lancement suivant s'il a plus de deux heures.
TEMPROOT="${TMPDIR:-/tmp}"
find "$TEMPROOT" -maxdepth 1 -type d -name 'mosaicview_tlh.*' -mmin +120 -exec rm -rf {} + 2>/dev/null || true
LOGS="$(mktemp -d "$TEMPROOT/mosaicview_tlh.XXXXXX")"
DONE=0
finish() {
  status=$?
  # Échec après la sauvegarde : fichiers de langue remis dans leur état d'avant le bloc
  if [ "$DONE" = 0 ] && [ -f "$LOGS/tlh.json" ]; then
    cp "$LOGS/tlh.json" locales/tlh.json
    cp "$LOGS/tlh-piqad.json" locales/tlh-piqad.json
    echo "ECHEC : locales/tlh.json et locales/tlh-piqad.json remis dans leur etat d'avant le bloc"
    if [ -s "$LOGS/last_piqad.log" ]; then cat "$LOGS/last_piqad.log"; fi
  fi
  rm -rf "$LOGS"
  exit $status
}
trap finish EXIT

n=$(python -I "$TOOLS/check_tlh.py" "$TOOLS/boqwi_index.tsv" "$BLOCK" "$LOGS/last_check.txt" "$LOGS/last_decoupages.txt")
if [ "$n" != "0" ]; then
  echo "CONTROLE LEXICAL : $n mot(s) non reconnu(s)"; cat "$LOGS/last_check.txt"; exit 1
fi
# Mots reconnus par découpage : relire le sens de chaque racine, une décomposition valide
# pouvant donner un autre mot que celui voulu.
if [ -s "$LOGS/last_decoupages.txt" ]; then
  echo "DECOUPAGES A RELIRE :"; cat "$LOGS/last_decoupages.txt"
fi
python "$TOOLS/check_struct.py" "$BLOCK" "$@"

cp locales/tlh.json "$LOGS/tlh.json"
cp locales/tlh-piqad.json "$LOGS/tlh-piqad.json"

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
DONE=1
