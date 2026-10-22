"""
Convertit sjn-tengwar.json et qya-tengwar.json :
texte latin → codepoints CSUR Tengwar via Glaemscribe (Node.js).

Les valeurs non-linguistiques (acronymes CBZ/PDF, symboles, clés {param},
chiffres) sont préservées telles quelles.
"""
import json
import re
import subprocess
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE_SCRIPT = os.path.join(BASE, 'glaemscribe_js', 'transcribe.js')
CHARSET = 'tengwar_freemono'

# Mots/tokens à ne pas transcrire (acronymes, symboles, etc.)
# On découpe le texte en segments : {param}, acronymes connus, et texte linguistique
PRESERVE_RE = re.compile(
    r'(\{[^}]+\}'                                      # {param}
    r'|https?://\S+'                                    # URLs
    r'|MosaicView|mosaicview'                          # nom de l'appli
    r'|Log_[A-Za-z0-9_]+'                              # noms des fichiers journaux (Log_scan.txt, Log_pdftocbz_…)
    r'|config|json'                                    # mots techniques dans noms de fichiers
    r'|CBZ|CBR|CB7|CBT|PDF|GIF|MP|DPI'                # acronymes formats/tech
    r'|cbz|cbr|cb7|cbt|pdf|gif|epub|EPUB'             # idem minuscules (+ EPUB majuscule, extension isolée)
    r'|ZIP|RAR|7Z|7z|XLSX'                             # acronymes archives/fichiers
    r'|TrueType'                                       # format de police
    r'|\bAI\b'                                         # sigle IA (bornes de mot : ne pas couper ÚVAIN, ÚNAVAITË...)
    r'|7-Zip|7-zip'                                    # nom application 7-Zip
    r'|JPEG|jpeg|JPG|jpg|PNG|png|WEBP|webp'           # formats image courants
    r'|[Ii]con'                                         # terme technique "icône" (avant ICO|ico : "icon" ⊃ "ico")
    r'|BMP|bmp|TIFF|tiff|tif|ICO|ico'                 # formats image suite
    r'|AVIF|avif'                                      # format image AVIF
    r'|jfif|pjpeg|pjp|JFIF|PJPEG|PJP'               # variantes JPEG
    r'|Windows|DPAPI'                                  # termes techniques Windows
    r'|%APPDATA%'                                      # dossier Windows cité dans un chemin
    r'|PyMuPDF|fitz'                                   # bibliothèque PDF
    r'|YACReader'                                      # nom application
    r'|pIqaD|piqad|qaSpu|qolqoS'                      # termes klingon
    r'|CDisplayEx|Excel|Explorer'                      # noms d'applications
    r'|ComicVine|ComicInfo'                            # noms techniques ComicVine/ComicInfo
    r'|Assets|Releases'                                 # sections page GitHub releases
    r'|Claude Code|mvdb'                                # produit IA, extension des bases de la bibliothèque
    r'|\bgamma\b|\bbitmap\b|\bbit\b'                    # termes techniques (correction gamma, BMP bitmap, 64-bit)
    r'|Distillat|Gemini|Flash'                          # nom application tierce, modèle IA et son suffixe (fenêtre "Publicité éhontée")
    r'|Webtoon|webtoon'                                 # mode de lecture visionneuse
    r'|GNU|LGPL|GPL'                                    # licences logicielles
    r'|extension|ext|pixels?|px|txt|xml'               # extensions/termes techniques
    r'|XXXXX'                                          # placeholder
    r'|Ctrl\+(?:Alt\+|Shift\+)?[A-Za-z0-9]'           # raccourcis clavier
    r'|Alt|Shift'                                      # modificateurs clavier
    r'|(?<![A-Za-z])X(?![A-Za-z])'                    # X isolé (X18+)
    r'|\*'                                              # astérisque (filtres fichiers)
    r'|[↷↶⇄⇅⤺⤻↻↺←→«»©×•⚠️✓…–—−≠≥≤☠]'             # symboles UI (· exclu : géré par Glaemscribe)
    r'|️'                                         # sélecteur variation emoji
    r'|[/\-_.,;:!()\[\]{}@#%&*+=<>?|\\]'              # ponctuation ASCII
    r'|\n'                                             # sauts de ligne
    r'|\d+'                                            # chiffres
    r')'
)


def transcribe_batch(texts, mode):
    """Appelle Node.js pour transcrire une liste de textes."""
    result = subprocess.run(
        ['node', NODE_SCRIPT, mode, CHARSET],
        input=json.dumps(texts),
        capture_output=True,
        text=True,
        encoding='utf-8',
        cwd=BASE
    )
    if result.returncode != 0:
        raise RuntimeError(f"Node.js error: {result.stderr}")
    return json.loads(result.stdout)


def split_preserve(text):
    """
    Découpe un texte en segments : (segment, is_preserved).
    Les segments préservés passent tels quels, les autres sont transcrits.
    Le guillemet droit " est retiré de la sortie Tengwar : la police
    embarquée (AlcarinTengwarVF.ttf) a un glyphe quotedbl mal calibré,
    disproportionné à l'affichage — le texte source en romanisation latine
    (sjn.json/qya.json) n'est jamais concerné, il garde ses guillemets
    normalement.
    """
    text = text.replace('"', '')
    parts = PRESERVE_RE.split(text)
    result = []
    for part in parts:
        if not part:
            continue
        if PRESERVE_RE.fullmatch(part):
            result.append((part, True))
        else:
            result.append((part, False))
    return result


def convert_json(src_path, dest_path, mode):
    with open(src_path, encoding='utf-8') as f:
        data = json.load(f)

    # Collecte tous les textes à transcrire
    # On garde la structure (chemins) pour réinjecter après transcription
    texts_to_transcribe = []  # [(chemin, segment_index, texte)]

    # Clés de premier niveau à ne pas transcrire
    SKIP_TOP_KEYS = {'app_title', 'app_baseline'}
    # Clés (à n'importe quelle profondeur) contenant des titres de fenêtres Windows
    SKIP_ANY_KEYS = {'window_title', 'window_title_no_db', 'quality_window_title', 'icons_window_title'}

    def collect(obj, path):
        # Ne pas transcrire les clés de premier niveau exclues
        if len(path) == 1 and path[0] in SKIP_TOP_KEYS:
            return
        # Ne pas transcrire les clés de titre de fenêtre (à n'importe quelle profondeur)
        if path and path[-1] in SKIP_ANY_KEYS:
            return
        if isinstance(obj, str):
            segments = split_preserve(obj)
            for i, (seg, preserved) in enumerate(segments):
                if not preserved and seg.strip():
                    texts_to_transcribe.append((path, i, seg))
        elif isinstance(obj, dict):
            for k, v in obj.items():
                collect(v, path + [k])
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                collect(v, path + [i])

    collect(data, [])

    # Transcrit tous les segments en une seule passe Node.js
    raw_texts = [t for _, _, t in texts_to_transcribe]
    if raw_texts:
        transcribed = transcribe_batch(raw_texts, mode)
    else:
        transcribed = []

    # Map chemin+index → texte transcrit
    trans_map = {}
    for (path, seg_idx, _), result in zip(texts_to_transcribe, transcribed):
        key = (tuple(path), seg_idx)
        trans_map[key] = result

    # Reconstruit le JSON avec les segments transcrits
    def reconstruct(obj, path):
        if isinstance(obj, str):
            segments = split_preserve(obj)
            parts = []
            for i, (seg, preserved) in enumerate(segments):
                if preserved or not seg.strip():
                    parts.append(seg)
                else:
                    key = (tuple(path), i)
                    parts.append(trans_map.get(key, seg))
            return ''.join(parts)
        elif isinstance(obj, dict):
            return {k: reconstruct(v, path + [k]) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [reconstruct(v, path + [i]) for i, v in enumerate(obj)]
        return obj

    converted = reconstruct(data, [])

    with open(dest_path, 'w', encoding='utf-8') as f:
        json.dump(converted, f, ensure_ascii=False, indent=2)

    print(f"Converti : {dest_path} ({len(raw_texts)} segments transcrits)")


# --- Conversion ---
locales = os.path.join(BASE, 'locales')

convert_json(
    os.path.join(locales, 'sjn.json'),
    os.path.join(locales, 'sjn-tengwar.json'),
    'sindarin-tengwar-general_use'
)

convert_json(
    os.path.join(locales, 'qya.json'),
    os.path.join(locales, 'qya-tengwar.json'),
    'quenya-tengwar-classical'
)
