"""
Convertit tlh-piqad.json : romanisation okrandienne → codepoints CSUR pIqaD
Les caractères non-klingon (espaces, chiffres latins, ponctuation ASCII,
symboles spéciaux ↷ ↶ ⇄ etc.) sont conservés tels quels.
"""
import json
import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Acronymes/noms techniques à préserver tels quels (non traduits en pIqaD).
# Certains phonèmes klingon (D, H, I, Q, S, tlh, ch, gh, ng) coïncident avec
# des lettres latines : sans cette protection, "ZIP" devient partiellement
# converti (le "I" est pris pour le phonème klingon I). Cette liste doit être
# tenue à jour manuellement à chaque ajout de terme technique dans tlh.json —
# les mots klingon eux-mêmes (même en MAJUSCULES d'emphase, ex: CHEGHLABBE,
# VOQMOH, TIVQA' RIGHTS) ne doivent PAS être ajoutés ici : ils sont censés
# être convertis en CSUR.
PRESERVE_RE = re.compile(
    r'(\{[^}]+\}'                                       # {param}
    r'|https?://\S+'                                     # URLs
    r'|comicvine\.gamespot\.com|comicvine\.com'         # domaines ComicVine (sans schéma)
    r'|MosaicView|mosaicview'                           # nom de l'appli
    r'|ComicVine|ComicInfo'                              # noms techniques ComicVine/ComicInfo
    r'|Log_[A-Za-z0-9_]+'                                # noms des fichiers journaux (Log_scan.txt, Log_pdftocbz_…)
    r'|config|json|xml'                                  # mots techniques fichiers
    r'|CBZ|CBR|CB7|CBT|PDF|GIF|MP|DPI'                  # acronymes formats/tech
    r'|cbz|cbr|cb7|cbt|pdf|gif|epub|EPUB'               # idem minuscules
    r'|ZIP|Zip|zip|RAR|7Z|7z|XLSX'                       # acronymes archives/fichiers
    r'|TrueType'                                         # format de police
    r'|7-Zip|7-zip|RARlab|UnRAR'                        # noms applications compression
    r'|JPEG|jpeg|JPG|jpg|PNG|png|WEBP|webp'             # formats image courants
    r'|[Ii]con'                                          # terme technique "icône" (avant ICO|ico : "icon" ⊃ "ico")
    r'|BMP|bmp|TIFF|tiff|tif|ICO|ico'                   # formats image suite
    r'|AVIF|avif'                                        # format image AVIF
    r'|jfif|pjpeg|pjp|JFIF|PJPEG|PJP'                 # variantes JPEG
    r'|Windows|DPAPI'                                    # termes techniques Windows
    r'|PyMuPDF|fitz|PIL'                                 # bibliothèques Python images/PDF
    r'|pywin32|win32print'                              # bibliothèques Python Windows
    r'|YACReader|CDisplayEx|Excel|Explorer|GitHub'      # noms d'applications
    r'|Assets|Releases'                                  # sections page GitHub releases
    r'|Webtoon|webtoon'                                   # mode de lecture visionneuse
    r'|Anthropic|Claude|Code'                            # nom société/produit IA (Claude Code)
    r'|mvdb'                                             # extension des bases de la bibliothèque
    r'|gamma|bitmap|bit'                                 # termes techniques (correction gamma, BMP bitmap, 64-bit)
    r'|Distillat|Gemini|Flash'                           # nom application tierce, modèle IA et son suffixe (fenêtre "Publicité éhontée")
    r'|pIqaD|piqad|qaSpu|qolqoS'                        # termes klingon
    r'|GNU|GPL|LGPL'                                     # licences logicielles
    r'|GTIN|ISO|API|AI|URL|RAM|RGBA?|CMYK|TAR'          # acronymes techniques divers
    r'|KB|MB'                                            # unités de taille fichier
    r'|F1[0-9]|F[1-9](?![0-9])'                         # touches de fonction (F5, F11...)
    r'|ESC'                                              # touche Echap
    r'|MA15|R18|PG(?![A-Za-z])'                         # classifications d'âge
    r'|extension|ext|pixels?|px|txt'                    # extensions/termes techniques
    r'|XXXXX'                                            # placeholder
    # Raccourcis clavier : touche = majuscule seule, chiffre, Plus ou Minus,
    # préservés avec Ctrl+. Suivi d'un mot klingon (ex. Ctrl+gho, Ctrl+'uy),
    # seul "Ctrl+" est préservé : le mot doit être transcrit, sinon sa
    # première lettre resterait en latin et le reste serait mal découpé.
    r'|Ctrl\+(?:Alt\+|Shift\+)?(?:Plus|Minus|[A-Z0-9](?![a-z]))'
    r'|Ctrl\+?'
    r'|Plus|Minus'                                       # touches + et - (Ctrl+Plus/Minus)
    r'|Alt|Shift'                                        # modificateurs clavier
    r'|(?<![A-Za-z])X(?![A-Za-z])'                      # X isolé (X18+)
    r')'
)

# Table de correspondance okrandien → CSUR
# Ordre CRITIQUE : les digraphes d'abord (tlh > ng/gh/ch > lettres simples)
MAPPING = [
    ("tlh", "\uF8E4"),
    ("ch",  "\uF8D2"),
    ("gh",  "\uF8D5"),
    # "ngh" se lit toujours n + gh, jamais ng + h : le klingon n'a pas de h
    # minuscule isol\u00E9 (seulement dans ch, gh et tlh). Sans cette entr\u00E9e, "ng"
    # passerait d'abord et laisserait un "h" latin (ex. tIn + ghach).
    ("ngh", "\uF8DB\uF8D5"),
    ("ng",  "\uF8DC"),
    ("a",   "\uF8D0"),
    ("b",   "\uF8D1"),
    ("D",   "\uF8D3"),
    ("e",   "\uF8D4"),
    ("H",   "\uF8D6"),
    ("I",   "\uF8D7"),
    ("j",   "\uF8D8"),
    ("l",   "\uF8D9"),
    ("m",   "\uF8DA"),
    ("n",   "\uF8DB"),
    ("o",   "\uF8DD"),
    ("p",   "\uF8DE"),
    ("q",   "\uF8DF"),
    ("Q",   "\uF8E0"),
    ("r",   "\uF8E1"),
    ("S",   "\uF8E2"),
    ("t",   "\uF8E3"),
    ("u",   "\uF8E5"),
    ("v",   "\uF8E6"),
    ("w",   "\uF8E7"),
    ("y",   "\uF8E8"),
    ("'",   "\uF8E9"),
]

def to_piqad(text):
    """
    Convertit un texte en romanisation okrandienne vers CSUR pIqaD.
    Les caractères non mappés (espaces, ponctuation, chiffres, etc.) sont
    conservés tels quels. Les paramètres {nom} et les tokens de PRESERVE_RE
    (acronymes, noms d'applications...) sont préservés intacts.
    Le guillemet droit " est retiré de la sortie CSUR : la police pIqaD
    embarquée n'a pas de glyphe pour ce caractère (bascule sur une police
    système de secours, rendu disproportionné) — le texte source en
    romanisation latine (tlh.json) n'est jamais concerné, il garde ses
    guillemets normalement.
    """
    text = text.replace('"', '')
    # Découpe le texte en segments préservés (PRESERVE_RE) vs texte à convertir
    parts = PRESERVE_RE.split(text)
    result = []
    for part in parts:
        if not part:
            continue
        if PRESERVE_RE.fullmatch(part):
            # Token préservé (paramètre, acronyme, symbole...) : conserver tel quel
            result.append(part)
        else:
            i = 0
            while i < len(part):
                matched = False
                for okrand, csur in MAPPING:
                    if part[i:i+len(okrand)] == okrand:
                        result.append(csur)
                        i += len(okrand)
                        matched = True
                        break
                if not matched:
                    result.append(part[i])
                    i += 1
    return "".join(result)


# Clés de premier niveau à ne pas convertir (titres fenêtre Windows)
SKIP_TOP_KEYS = {'app_title', 'app_baseline'}
# Clés à n'importe quelle profondeur à ne pas convertir (titres fenêtre Windows)
SKIP_ANY_KEYS = {'window_title', 'window_title_no_db', 'quality_window_title', 'icons_window_title'}

def convert_value(v, key=None, depth=0, top_key=None):
    """Convertit récursivement les valeurs string d'un dict/list JSON."""
    if isinstance(v, str):
        return to_piqad(v)
    elif isinstance(v, dict):
        result = {}
        for k, val in v.items():
            tk = k if depth == 0 else top_key
            if depth == 0 and k in SKIP_TOP_KEYS:
                result[k] = val
            elif k in SKIP_ANY_KEYS:
                result[k] = val
            else:
                result[k] = convert_value(val, key=k, depth=depth+1, top_key=tk)
        return result
    elif isinstance(v, list):
        return [convert_value(item, depth=depth+1, top_key=top_key) for item in v]
    return v


# --- Test rapide ---
def test():
    cases = [
        ("De'wI'", None),   # D e ' w I '
        ("HoS",    None),   # H o S
        ("tlhIngan", None), # tlh I ng a n
        ("ghItlh",  None),  # gh I tlh
        ("ngop",    None),  # ng o p
        ("qIp",     None),  # q I p
        ("Qapla'",  None),  # Q a p l a '
        ("chu'",    None),  # ch u '
    ]
    print("=== Tests de conversion ===")
    for okrand, _ in cases:
        csur = to_piqad(okrand)
        codepoints = " ".join(f"U+{ord(c):04X}" for c in csur)
        print(f"  {okrand!r:15} -> {codepoints}")
    print()

test()

# --- Conversion du fichier ---
src_latin = os.path.join(_ROOT, "locales", "tlh.json")
src = os.path.join(_ROOT, "locales", "tlh-piqad.json")

with open(src_latin, "r", encoding="utf-8") as f:
    data = json.load(f)

converted = convert_value(data)

with open(src, "w", encoding="utf-8") as f:
    json.dump(converted, f, ensure_ascii=False, indent=2)

print(f"Fichier converti : {src}")

# --- Vérification : aucun caractère latin klingon résiduel ---
# On cherche les lettres minuscules a-z et majuscules D H I Q S
# qui seraient des phonèmes klingon non convertis
import re

with open(src, "r", encoding="utf-8") as f:
    raw = f.read()

# Extrait toutes les valeurs string du JSON pour vérification
# (on ignore les clés et les valeurs contenant des acronymes connus)
suspicious = []
for m in re.finditer(r'"[^"]*"', raw):
    val = m.group(0)[1:-1]
    # Cherche des séquences qui ressemblent à du klingon résiduel
    # (lettres latines dans un contexte majoritairement CSUR)
    latin_in_csur = re.findall(r'[a-z]', val)
    if latin_in_csur:
        # Filtre les valeurs qui sont purement des labels/clés (pas des valeurs traduites)
        csur_chars = [c for c in val if '\uF8D0' <= c <= '\uF8FF']
        if csur_chars:  # Mixte latin + CSUR = suspect
            suspicious.append((val[:60], latin_in_csur[:5]))

if suspicious:
    print("\n[!] Valeurs mixtes latin+CSUR (potentiellement des conversions partielles) :")
    for val, chars in suspicious[:10]:
        print(f"  {val!r} -> latin residuel: {chars}")
else:
    print("[OK] Aucune valeur mixte latin+CSUR detectee.")
