"""
Règles de nommage des membres d'archives, communes à tous les lecteurs :
ouverture (archive_loader), ajout (import_merge_qt), conversions par lot
(batch_dialogs_qt) et import de métadonnées en lot (batch_metadata_dialog_qt).

Bibliothèque standard seule, aucun import Qt : les traitements par lot et les
tests l'importent sans charger le chargeur d'archives. Une règle de nommage
appliquée par un seul lecteur fait diverger le fichier produit selon le
chemin emprunté (ouverture puis enregistrement, ou conversion en lot) : elle
se place ici.
"""


import posixpath
import re
import unicodedata
import zipfile
from urllib.parse import unquote


# Drapeau « nom en UTF-8 » d'un membre ZIP (bit 11 des drapeaux généraux)
_ZIP_UTF8_FLAG = 0x800

# Caractères non latins tolérés dans un nom lu correctement
_NAME_SYMBOLS = "°«»–—’‘“”€½¼¾·"


def _looks_misread(name):
    """True si `name` contient des caractères qu'un nom de fichier ne contient
    pratiquement jamais (traits de cadre, lettres grecques, symboles
    mathématiques) : signe d'un nom lu avec le mauvais codage."""
    for char in name:
        if char.isascii() or char in _NAME_SYMBOLS:
            continue
        if not unicodedata.name(char, "").startswith("LATIN"):
            return True
    return False


def _mixed_case(name):
    """True si une majuscule non ASCII suit directement une minuscule
    (`cafÚ`, `crÞme`) : forme qu'un vrai nom n'a pratiquement jamais, et que
    produit un nom cp1252 lu en cp850."""
    return any(name[i].isupper() and not name[i].isascii() and name[i - 1].islower()
               for i in range(1, len(name)))


def _plausible_name(name):
    return "�" not in name and not _looks_misread(name) and not _mixed_case(name)


def zip_member_name(name):
    """Nom réel d'un membre ZIP écrit sans le drapeau UTF-8, que Python a lu
    en cp437 (`name`) comme le veut la norme ZIP.

    Beaucoup de logiciels écrivent pourtant ces noms autrement : en UTF-8
    sans le drapeau (outils Mac et Linux, scripts), en cp850 (dossier
    compressé de Windows en Europe de l'Ouest : les majuscules accentuées y
    diffèrent du cp437) ou en cp1252 (Windows « ANSI »). Lus en cp437, `Été`
    devenait `├ët├⌐`, `L'Île` `L'╫le`, `café` `cafΘ` ; l'ordre des pages en
    était faussé, et le premier enregistrement écrivait définitivement le
    nom abîmé.
    - UTF-8 valide avec des caractères non ASCII : UTF-8 (une telle suite
      d'octets n'apparaît pratiquement jamais par hasard dans un nom cp437) ;
    - nom cp437 peu plausible (traits de cadre, lettres grecques, majuscule
      accentuée au milieu d'un mot) : cp850 s'il donne un nom plausible,
      sinon cp1252 s'il en donne un ;
    - sinon `name` inchangé.
    Limite connue : un nom cp1252 dont la lettre accentuée commence un mot
    (`épisode`, `Ça`) est plausible en cp850 et lu ainsi (`Úpisode`) ;
    aucun critère sûr ne départage ces deux codages dans ce cas."""
    try:
        raw = name.encode("cp437")
    except UnicodeEncodeError:
        return name
    if raw.isascii():
        return name
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    if _looks_misread(name) or _mixed_case(name):
        for encoding in ("cp850", "cp1252"):
            candidate = raw.decode(encoding, errors="replace")
            if _plausible_name(candidate):
                return candidate
    return name


def fix_zip_names(zf):
    """Corrige sur place, dans le ZipFile ouvert en lecture `zf`, le nom des
    membres écrits sans le drapeau UTF-8 (zip_member_name) : `filename` de
    chaque ZipInfo et table des noms (NameToInfo, utilisée par read(nom)),
    de sorte que namelist(), infolist() et la lecture par nom donnent tous
    le nom réel. `orig_filename` reste celui lu par Python : zipfile le
    compare à l'en-tête local du membre à chaque lecture. Rend `zf`."""
    changed = False
    for info in zf.infolist():
        if info.flag_bits & _ZIP_UTF8_FLAG:
            continue
        fixed = zip_member_name(info.filename)
        if fixed != info.filename:
            info.filename = fixed
            changed = True
    if changed:
        # Même règle que zipfile : le dernier homonyme l'emporte
        zf.NameToInfo = {info.filename: info for info in zf.infolist()}
    return zf


def open_zip(file):
    """ZipFile ouvert en lecture sur `file` (chemin ou fichier), noms des
    membres corrigés (fix_zip_names). À utiliser pour toute archive dont les
    noms sont affichés, deviennent des noms de pages ou sont réécrits dans
    un CBZ : tous les lecteurs donnent ainsi les mêmes noms."""
    zf = zipfile.ZipFile(file, "r")
    try:
        return fix_zip_names(zf)
    except BaseException:
        zf.close()
        raise


def _natural_key(name):
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', name)]


def epub_reading_order(zf, image_names):
    """Les images `image_names` (noms de membres du ZIP `zf`) d'un EPUB, dans
    l'ordre de lecture du livre : ordre des documents de la « spine » de
    l'OPF, puis ordre d'apparition des images dans chaque document. Les
    images qu'aucun document n'affiche suivent, par nom (l'image de
    couverture déclarée dans l'OPF passe en tête). Ordre des noms seul si
    l'EPUB est incomplet ou illisible.

    Le tri par nom ne suit pas le livre : les images d'un EPUB portent des
    noms arbitraires (cover.jpg, image_12.png, img-a.jpg…).

    Analyse par expressions régulières, sans parseur XML : aucune entité
    externe d'un EPUB téléchargé n'est résolue. Membres lus par leur ZipInfo
    (le premier de chaque nom), jamais par nom."""
    by_name = sorted(image_names, key=_natural_key)
    infos = {}
    for info in zf.infolist():
        infos.setdefault(info.filename, info)

    def _read(path):
        info = infos.get(path)
        return zf.read(info).decode("utf-8", errors="replace") if info else ""

    def _resolve(base_dir, href):
        href = unquote(href.split("#", 1)[0].strip())
        return posixpath.normpath(posixpath.join(base_dir, href)) if href else ""

    try:
        match = re.search(r'full-path\s*=\s*["\']([^"\']+)["\']', _read("META-INF/container.xml"))
        if not match:
            return by_name
        opf_path = match.group(1)
        opf = _read(opf_path)
        opf_dir = posixpath.dirname(opf_path)
        manifest, cover = {}, None
        for item in re.findall(r'<(?:\w+:)?item\b[^>]*>', opf):
            ident = re.search(r'\bid\s*=\s*["\']([^"\']+)["\']', item)
            href = re.search(r'\bhref\s*=\s*["\']([^"\']+)["\']', item)
            if ident and href:
                manifest[ident.group(1)] = _resolve(opf_dir, href.group(1))
                if re.search(r'properties\s*=\s*["\'][^"\']*cover-image', item):
                    cover = manifest[ident.group(1)]
        images = set(image_names)
        order = []
        for idref in re.findall(r'<(?:\w+:)?itemref\b[^>]*\bidref\s*=\s*["\']([^"\']+)["\']', opf):
            doc_path = manifest.get(idref)
            if not doc_path:
                continue
            if doc_path in images:
                found = [doc_path]
            else:
                doc_dir = posixpath.dirname(doc_path)
                found = [_resolve(doc_dir, src) for src in re.findall(
                    r'<(?:\w+:)?(?:img|image)\b[^>]*?\b(?:src|xlink:href|href)\s*=\s*["\']([^"\']+)["\']',
                    _read(doc_path))]
            for path in found:
                if path in images and path not in order:
                    order.append(path)
    except Exception:
        return by_name
    rest = [n for n in by_name if n not in order]
    if cover in rest:
        rest.remove(cover)
        order.insert(0, cover)
    return order + rest


def epub_page_name(position, member_name):
    """Nom d'une page lue dans un EPUB : son rang dans l'ordre de lecture
    (epub_reading_order, à partir de 1) devant son nom de base. Sans ce rang,
    l'ordre de lecture serait perdu dès que les pages sont triées par nom
    (ouverture de plusieurs archives, ajout à un comic, et tout lecteur du
    CBZ enregistré)."""
    return f"{position:04d}_{posixpath.basename(member_name)}"


# Taille maximale d'un membre d'archive lu en mémoire (500 Mo, la limite déjà
# appliquée à une image ouverte seule). Source unique pour tous les lecteurs :
# archive_loader._check_member_size (ouverture, ajout, lots, import de
# métadonnées) et la Bibliothèque (library_db), testée sans Qt. La taille
# annoncée par l'archive est contrôlée avant la lecture (ZIP, RAR, TAR), la
# taille reçue pendant l'extraction pour un CB7 : un membre de quelques Ko qui
# se décompresse en plusieurs Go (« bombe de décompression »), ou un membre
# TAR « creux » de taille annoncée énorme, épuiseraient la mémoire.
MAX_MEMBER_SIZE = 500 * 1024 * 1024


# Extensions des fichiers traités comme des images (pages) par toute
# l'application : source unique, importée par chaque module qui en a besoin.
# Une copie locale oubliée lors de l'ajout d'un format le ferait traiter comme
# un fichier ordinaire à cet endroit seulement.
IMAGE_EXTS = ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp',
              '.tiff', '.tif', '.ico', '.jfif', '.pjpeg', '.pjp', '.avif')

# Synonymes de JPEG (JFIF, JPEG progressif) : mêmes règles d'encodage que .jpg
JPEG_EXTS = (".jpg", ".jpeg", ".jfif", ".pjpeg", ".pjp")

# Extensions acceptées pour chaque format d'image réel (Image.format de
# Pillow) ; la première est celle donnée à une page mal nommée.
_FORMAT_EXTENSIONS = {
    "JPEG": JPEG_EXTS,
    "MPO": JPEG_EXTS,            # JPEG d'appareil photo avec une image annexe
    "PNG": (".png",),
    "WEBP": (".webp",),
    "GIF": (".gif",),
    "BMP": (".bmp",),
    "DIB": (".bmp",),
    "TIFF": (".tif", ".tiff"),
    "AVIF": (".avif",),
    "ICO": (".ico",),
}


def restrict_pillow_formats():
    """Limite les formats que Pillow reconnaît à l'ouverture d'une image à ceux
    que MosaicView gère (_FORMAT_EXTENSIONS ; MPO passe par le décodeur JPEG).

    Image.open choisit le décodeur d'après le contenu, parmi plus de 40
    formats : une page nommée .jpg qui contient en réalité un EPS serait
    rendue par Ghostscript, s'il est installé (programme externe aux failles
    connues), et les décodeurs de formats rares élargissent la surface
    d'attaque des fichiers téléchargés. Une telle page devient illisible
    (cadre rouge). À appeler une fois au démarrage, avant toute ouverture
    d'image ; l'enregistrement dans les autres formats n'est pas concerné.
    Image.ID est modifiée sur place : Image.open la compare par identité."""
    from PIL import Image
    Image.init()
    Image.ID[:] = [fmt for fmt in Image.ID if fmt in _FORMAT_EXTENSIONS]


def name_for_real_format(name, pil_format):
    """`name` avec l'extension du format réel de l'image (`pil_format` :
    Image.format de Pillow), quand son extension en annonce un autre (PNG
    nommé .jpg, WebP nommé .png…) ; inchangé si l'extension correspond déjà,
    ou si le format n'est pas un format d'image géré. Une page mal nommée
    serait réenregistrée dans le format de son extension à la première
    retouche (un PNG transparent devenait un JPEG, transparence et qualité
    perdues), et les lecteurs se fient à l'extension."""
    extensions = _FORMAT_EXTENSIONS.get(pil_format)
    stem, ext = posixpath.splitext(name)
    if not extensions or ext.lower() in extensions:
        return name
    return stem + extensions[0]


def same_image_format(ext_a, ext_b):
    """True si les extensions ext_a et ext_b (avec le point) désignent le même
    format d'image (.jpg et .jpeg, .tif et .tiff…), ou sont identiques."""
    a, b = ext_a.lower(), ext_b.lower()
    if a == b:
        return True
    return any(a in exts and b in exts for exts in _FORMAT_EXTENSIONS.values())


_EPUB_RANK_PREFIX = re.compile(r"^(?:NEW-)?\d{4,}_")


def without_epub_rank(page_name):
    """Nom de base d'une page d'EPUB sans son rang de lecture
    (epub_page_name), ni le préfixe NEW- d'une archive ajoutée ; page_name
    inchangé s'il n'en porte pas."""
    return _EPUB_RANK_PREFIX.sub("", page_name, count=1)


def tar_entry_name(member_name):
    """Nom d'une page lue dans un CBT : chemin complet dans l'archive, comme
    pour CBZ/CBR/CB7, sans le préfixe "./" ou "/" que certains outils ajoutent.
    Jamais le seul nom de base : deux pages homonymes de dossiers différents
    porteraient le même nom, et la structure de l'archive serait perdue à sa
    conversion en CBZ. Sans normalisation, le CBZ produit contiendrait un faux
    sous-dossier « . » à aplatir avant tout enregistrement."""
    name = member_name.replace("\\", "/")
    while name.startswith("./"):
        name = name[2:]
    return name.lstrip("/")


def without_control_chars(name):
    """`name` dont chaque caractère de contrôle (retour à la ligne, tabulation…)
    devient « _ », comme dans un nom écrit sur le disque
    (utils.windows_safe_component). Un tel caractère n'a rien à faire dans un
    nom de page : Windows le refuse dans un nom de fichier, et d'autres
    lecteurs de BD échoueraient à extraire la page."""
    return "".join("_" if ord(c) < 32 else c for c in name)


def contained_member_name(member_name):
    """Nom d'un membre d'archive à écrire dans un CBZ produit par conversion,
    sans rien qui le ferait sortir du dossier où un lecteur extrait l'archive :
    segments « .. » et « . », barre initiale, lettre de lecteur (« C: ») et
    chemin réseau (« //serveur ») retirés ; sous-dossiers conservés, « \\ »
    lu comme « / », caractères de contrôle remplacés (without_control_chars).
    Un nom ordinaire est rendu inchangé. "" s'il ne reste rien.

    Un nom piégé (« ../x.jpg ») venu d'une archive téléchargée ne gêne pas
    MosaicView, qui n'extrait jamais d'archive sur le disque, mais d'autres
    lecteurs de BD l'écriraient hors de leur dossier."""
    # « C:x.jpg » (relatif au lecteur, sans barre) comme « C:/x.jpg »
    name = re.sub(r"^[A-Za-z]:", "", without_control_chars(member_name).replace("\\", "/"))
    return "/".join(p for p in name.split("/") if p not in ("", ".", ".."))
