# -------------------------
# Parsing et gestion des métadonnées ComicInfo.xml
# -------------------------
import functools
import os
import re
import tarfile
import xml.etree.ElementTree as ET

from modules.qt.archive_names import windows_text
try:
    from defusedxml.ElementTree import fromstring as _safe_fromstring
except ImportError:
    _safe_fromstring = ET.fromstring

try:
    import rarfile
except ImportError:
    rarfile = None


def _without_namespaces(root):
    """Noms de balises et d'attributs sans namespace. Avec un xmlns par
    défaut, ElementTree nomme les balises « {uri}Title » : find("Title") ne
    les trouverait pas, et _serialize_comic_xml écrirait « <{uri}Title> »,
    qui n'est pas du XML. La balise <ComicInfo> réécrite à partir des bytes
    d'origine garde le xmlns : les balises sans préfixe restent dans le
    même namespace."""
    for elem in root.iter():
        if isinstance(elem.tag, str) and elem.tag.startswith('{'):
            elem.tag = elem.tag.split('}', 1)[1]
        for key in [k for k in elem.attrib if k.startswith('{')]:
            elem.attrib[key.split('}', 1)[1]] = elem.attrib.pop(key)
    return root


def _as_bytes(data):
    return data.encode("utf-8") if isinstance(data, str) else data


def comic_info_strictly_readable(data):
    """Le ComicInfo.xml est lu tel quel par le parseur durci, sans
    réparation (_repaired_comic_xml). Un fichier vide (0 octet) compte comme
    lisible : il n'y a rien à garder."""
    data = _as_bytes(data)
    if not data:
        return True
    try:
        _safe_fromstring(data)
        return True
    except Exception:
        return False


def _xml_bytes_for_repair(data):
    """Octets à donner à lxml : ceux du fichier, sauf s'il n'est pas en UTF-8
    sans le déclarer, où il est réécrit en UTF-8. Sans cela, lxml en mode
    réparation retombe de lui-même sur latin-1, pour tout le fichier.

    Un fichier qui contient au moins un caractère UTF-8 valide est un UTF-8 coupé
    ou abîmé : seuls ses octets invalides sont remplacés (le relire en cp1252 ou
    en latin-1 abîmerait tous ses accents). Sinon, il est en codage Windows
    (archive_names.windows_text)."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff") or re.match(rb"\s*<\?xml[^>]*encoding", data):
        return data
    try:
        data.decode("utf-8")
        return data
    except UnicodeDecodeError:
        pass  # repli : pas en UTF-8 valide, examiné ci-dessous
    if any(ord(c) > 127 for c in data.decode("utf-8", errors="ignore")):
        return data.decode("utf-8", errors="replace").encode("utf-8")
    return windows_text(data).encode("utf-8")


def _repaired_comic_xml(data):
    """Version réparée d'un ComicInfo.xml que le parseur durci refuse (XML
    abîmé, DTD ou déclaration d'entités), réécrite en XML propre ; None si
    rien n'en est récupérable (vide, racine absente ou d'un autre nom).

    lxml en mode réparation garde tout ce qui se lit encore (champs,
    balises d'autres logiciels, <Pages>), sans jamais exécuter une entité
    (resolve_entities=False : ni expansion en cascade qui remplirait la
    mémoire, ni lecture d'un fichier du disque ou d'une adresse réseau),
    sans charger de DTD ni accéder au réseau, avec les limites de taille
    par défaut de libxml2 (huge_tree=False). Les références d'entités
    restées dans le texte sont retirées : la déclaration a disparu, le
    parseur durci les refuserait. Un fichier qui n'est pas en UTF-8 sans
    le déclarer est relu par _xml_bytes_for_repair."""
    data = _as_bytes(data)
    if not data or not data.strip():
        return None
    try:
        from lxml import etree
    except ImportError:
        return None

    def _recover(raw):
        parser = etree.XMLParser(recover=True, resolve_entities=False, no_network=True,
                                 load_dtd=False, dtd_validation=False, huge_tree=False)
        return etree.fromstring(raw, parser)

    try:
        root = _recover(_xml_bytes_for_repair(data))
    except Exception:
        try:
            root = _recover(windows_text(data).encode("utf-8"))
        except Exception:
            return None
    if root is None or etree.QName(root).localname != "ComicInfo":
        return None
    for ent in list(root.iter(etree.Entity)):
        parent = ent.getparent()
        if parent is None:
            continue
        if ent.tail:
            prev = ent.getprevious()
            if prev is not None:
                prev.tail = (prev.tail or "") + ent.tail
            else:
                parent.text = (parent.text or "") + ent.tail
        parent.remove(ent)
    clean = etree.tostring(root, encoding="utf-8", xml_declaration=True)
    try:
        _safe_fromstring(clean)
    except Exception:
        return None
    return clean


def _comic_tree(data):
    """(arbre, bytes lus) d'un ComicInfo.xml : le parseur durci sur le
    fichier tel quel, et à défaut sur sa version réparée
    (_repaired_comic_xml), dont les bytes sont alors rendus pour que
    _serialize_comic_xml reprenne la balise <ComicInfo> réparée (la balise
    d'un fichier à DTD n'est pas relue). Lève ValueError si le fichier est
    irrécupérable."""
    data = _as_bytes(data)
    try:
        return _without_namespaces(_safe_fromstring(data)), data
    except Exception:
        pass  # repli : version réparée essayée juste en dessous
    clean = _repaired_comic_xml(data)
    if clean is None:
        raise ValueError("ComicInfo.xml unreadable")
    return _without_namespaces(_safe_fromstring(clean)), clean


def _comic_fromstring(data):
    """Arbre d'un ComicInfo.xml, réparé si besoin (_comic_tree), noms de
    balises et d'attributs sans namespace (_without_namespaces)."""
    return _comic_tree(data)[0]


# Caractères que XML 1.0 ne peut pas contenir, même échappés (contrôles sauf
# tabulation et fins de ligne, demi-caractères, U+FFFE et U+FFFF) : écrits,
# le ComicInfo.xml ne se relirait plus. Ils peuvent venir d'un texte de
# ComicVine ou d'un collage dans l'éditeur.
_XML_ILLEGAL_CHARS = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]')


@functools.lru_cache(maxsize=512)
def _writable_name(name):
    """Le nom de balise ou d'attribut se relit une fois écrit en UTF-8.
    expat accepte dans un fichier UTF-16 ou ISO-8859-1 des noms (contenant
    par exemple « µ ») qu'il refuse en UTF-8 : réécrits, ils rendaient le
    ComicInfo.xml illisible. Lu sans traitement des namespaces : seule la
    forme du nom compte."""
    from xml.parsers import expat
    try:
        expat.ParserCreate().Parse(f'<{name}/>'.encode('utf-8'), True)
    except Exception:
        return False
    return True


def _xml_text(value):
    """Texte d'élément XML échappé, sans les caractères que XML ne peut pas
    contenir (_XML_ILLEGAL_CHARS). Le retour chariot est écrit en référence
    de caractère : brut, la relecture le remplacerait par un saut de ligne."""
    return (_XML_ILLEGAL_CHARS.sub('', value).replace('&', '&amp;')
                 .replace('<', '&lt;')
                 .replace('>', '&gt;')
                 .replace('"', '&quot;')
                 .replace('\r', '&#13;'))


def _xml_attr(value):
    """Valeur d'attribut XML échappée. Les sauts de ligne et tabulations
    sont écrits en références de caractère : bruts, la relecture les
    remplacerait par des espaces (retour chariot : _xml_text)."""
    return (_xml_text(str(value))
            .replace('\n', '&#10;')
            .replace('\t', '&#9;'))


def _attrs_xml(elem, ordered_keys):
    """Attributs de elem, ceux de ordered_keys d'abord dans cet ordre ; un
    attribut dont le nom ne peut pas s'écrire (_writable_name) est omis."""
    extra_keys = [k for k in elem.attrib if k not in ordered_keys]
    all_keys = [k for k in ordered_keys if k in elem.attrib] + extra_keys
    return ''.join(f' {k}="{_xml_attr(elem.attrib[k])}"' for k in all_keys if _writable_name(k))


def _inline_xml(elem):
    """Élément écrit à l'identique sur une seule ligne : attributs,
    sous-éléments, textes et blancs compris (texte de queue d'un commentaire
    gardé, le commentaire lui-même non). Rien pour un élément dont le nom
    ne peut pas s'écrire (_writable_name)."""
    tag = elem.tag.split('}', 1)[-1]
    if not _writable_name(tag):
        return ""
    inner = _xml_text(elem.text or "")
    for child in elem:
        if isinstance(child.tag, str):
            inner += _inline_xml(child)
        inner += _xml_text(child.tail or "")
    return f'<{tag}{_attrs_xml(elem, [])}>{inner}</{tag}>'


def _element_lines(elem, indent):
    """Lignes XML d'un élément quelconque, écrit fidèlement : attributs,
    sous-éléments et textes. Un élément simple (ni attribut ni sous-élément,
    cas de tous les champs standard de ComicInfo) garde le format
    `<Tag>texte</Tag>` sur une ligne. Les balises ajoutées par d'autres
    logiciels (attributs, sous-balises) seraient sinon aplaties en simple
    texte à chaque réécriture du ComicInfo.xml. Entre sous-éléments, les
    seuls blancs sont remplacés par l'indentation. Un élément qui mêle texte
    et sous-éléments (texte non blanc avant ou entre eux) est écrit à
    l'identique sur une ligne (_inline_xml) : indenté, il recevait de
    nouveaux blancs dans son texte à chaque réécriture. Un élément dont le
    nom ne peut pas s'écrire (_writable_name) est omis, avec son contenu."""
    tag = elem.tag.split('}', 1)[-1]
    if not _writable_name(tag):
        return []
    attrs = _attrs_xml(elem, [])
    children = [c for c in elem if isinstance(c.tag, str)]
    if not children:
        return [f'{indent}<{tag}{attrs}>{_xml_text(elem.text or "")}</{tag}>']
    if (elem.text or "").strip() or any((c.tail or "").strip() for c in elem):
        return [indent + _inline_xml(elem)]
    lines = [f'{indent}<{tag}{attrs}>']
    for child in children:
        lines.extend(_element_lines(child, indent + '  '))
    lines.append(f'{indent}</{tag}>')
    return lines


def unmanaged_elements(root, managed_tags):
    """Éléments de premier niveau d'un ComicInfo.xml (arbre _comic_fromstring)
    que le formulaire d'édition ne gère pas — balises ajoutées par d'autres
    logiciels —, dans leur ordre : ni l'un des `managed_tags`, ni <Pages>, ni
    <MosaicViewTrace> (recopiés à part). Le formulaire les recopie telles
    quelles : reconstruit à partir de ses seuls champs, il les perdrait."""
    excluded = set(managed_tags) | {"Pages", "MosaicViewTrace"}
    return [child for child in root
            if isinstance(child.tag, str) and child.tag not in excluded]


class _RootTagRead(Exception):
    """Arrêt de la lecture par expat dès la balise racine (_comic_info_open_tag)."""


def _comic_info_open_tag(original_bytes):
    """Balise ouvrante <ComicInfo …> du document d'origine, attributs et
    déclarations xmlns compris, dans leur ordre. Relue par expat (sans
    traitement des namespaces, qui garde les xmlns comme attributs), jamais
    par une expression régulière, puis réécrite : une balise d'origine
    auto-fermante (<ComicInfo/>, ComicInfo.xml vide), un « > » dans un
    attribut ou un « <ComicInfo » dans un commentaire donnaient sinon un
    ComicInfo.xml illisible, et un attribut non UTF-8 faisait échouer
    l'écriture. Codage du fichier respecté (UTF-8, UTF-16, ISO-8859-1).
    '<ComicInfo>' si elle ne peut pas être relue (XML abîmé, DTD, racine
    d'un autre nom)."""
    from xml.parsers import expat
    found = []

    def _start(name, attrs):
        found.append((name, attrs))
        raise _RootTagRead

    def _doctype(*_args):
        raise _RootTagRead

    parser = expat.ParserCreate()
    parser.ordered_attributes = True
    parser.StartElementHandler = _start
    parser.StartDoctypeDeclHandler = _doctype
    try:
        parser.Parse(original_bytes, True)
    except Exception:
        pass  # repli : balise relue (_RootTagRead) ou illisible, '<ComicInfo>' sans attributs
    if not found or found[0][0] != 'ComicInfo':
        return '<ComicInfo>'
    attrs = found[0][1]
    return '<ComicInfo' + ''.join(
        f' {name}="{_xml_attr(value)}"' for name, value in zip(attrs[0::2], attrs[1::2])
        if _writable_name(name)) + '>'


def _serialize_comic_xml(root, original_bytes=None):
    """
    Sérialise un arbre ComicInfo en bytes avec le format exact attendu :
    - Déclaration : <?xml version="1.0"?>  (sans encoding dans la déclaration)
    - Fins de ligne : \\r\\n
    - Balise <ComicInfo ...> : attributs de celle d'original_bytes (xmlns inclus,
      _comic_info_open_tag)
    - Éléments enfants de <ComicInfo> : 2 espaces d'indentation
    - Éléments <Page> dans <Pages> : 4 espaces d'indentation, auto-fermants
    - Ordre des attributs de <Page> : Image, ImageSize, ImageWidth, ImageHeight, puis les autres
    """
    lines = ['<?xml version="1.0"?>']
    lines.append(_comic_info_open_tag(original_bytes) if original_bytes else '<ComicInfo>')

    for child in root:
        if not isinstance(child.tag, str):
            continue  # commentaire ou instruction de traitement
        tag = child.tag.split('}', 1)[-1]
        if tag == 'Pages':
            lines.append('  <Pages>')
            for page in child:
                # Ordre canonique des attributs
                attrs = _attrs_xml(page, ['Image', 'ImageSize', 'ImageWidth', 'ImageHeight'])
                lines.append(f'    <Page{attrs} />')
            lines.append('  </Pages>')
        elif tag == 'MosaicViewTrace':
            # Élément à attributs (date, url), auto-fermant — comme <Page>
            attrs = _attrs_xml(child, ['date', 'url'])
            lines.append(f'  <MosaicViewTrace{attrs} />')
        else:
            # Champ standard (texte seul) ou balise d'un autre logiciel,
            # écrite avec ses attributs et ses sous-balises
            lines.extend(_element_lines(child, '  '))

    lines.append('</ComicInfo>')

    content = '\r\n'.join(lines).encode('utf-8')
    # Dernier filet : un ComicInfo.xml illisible n'est jamais rendu (ParseError
    # levée, l'appelant garde l'ancien) ; il ferait perdre toutes les
    # métadonnées de la BD au prochain enregistrement
    _safe_fromstring(content)
    return content


def parse_comic_info_xml(xml_data):
    try:
        root = _comic_fromstring(xml_data)
        metadata = {}

        # Extraire les métadonnées principales
        metadata['title'] = root.findtext('Title', '')
        metadata['series'] = root.findtext('Series', '')
        metadata['number'] = root.findtext('Number', '')
        metadata['volume'] = root.findtext('Volume', '')
        metadata['summary'] = root.findtext('Summary', '')
        metadata['writer'] = root.findtext('Writer', '')
        metadata['penciller'] = root.findtext('Penciller', '')
        metadata['inker'] = root.findtext('Inker', '')
        metadata['colorist'] = root.findtext('Colorist', '')
        metadata['letterer'] = root.findtext('Letterer', '')
        metadata['cover_artist'] = root.findtext('CoverArtist', '')
        metadata['editor'] = root.findtext('Editor', '')
        metadata['publisher'] = root.findtext('Publisher', '')
        metadata['imprint'] = root.findtext('Imprint', '')
        metadata['genre'] = root.findtext('Genre', '')
        metadata['web'] = root.findtext('Web', '')
        metadata['page_count'] = root.findtext('PageCount', '')
        metadata['language_iso'] = root.findtext('LanguageISO', '')
        metadata['format'] = root.findtext('Format', '')
        metadata['black_and_white'] = root.findtext('BlackAndWhite', '')
        metadata['manga'] = root.findtext('Manga', '')
        metadata['year'] = root.findtext('Year', '')
        metadata['month'] = root.findtext('Month', '')
        metadata['day'] = root.findtext('Day', '')
        metadata['notes'] = root.findtext('Notes', '')
        metadata['characters'] = root.findtext('Characters', '')
        metadata['teams'] = root.findtext('Teams', '')
        metadata['locations'] = root.findtext('Locations', '')
        metadata['story_arc'] = root.findtext('StoryArc', '')
        metadata['story_arc_number'] = root.findtext('StoryArcNumber', '')
        metadata['series_group'] = root.findtext('SeriesGroup', '')
        metadata['count'] = root.findtext('Count', '')
        metadata['alternate_series'] = root.findtext('AlternateSeries', '')
        metadata['alternate_number'] = root.findtext('AlternateNumber', '')
        metadata['alternate_count'] = root.findtext('AlternateCount', '')
        metadata['age_rating'] = root.findtext('AgeRating', '')
        metadata['series_complete'] = root.findtext('SeriesComplete', '')
        metadata['translator'] = root.findtext('Translator', '')
        metadata['tags'] = root.findtext('Tags', '')
        metadata['scan_information'] = root.findtext('ScanInformation', '')
        metadata['community_rating'] = root.findtext('CommunityRating', '')
        metadata['review'] = root.findtext('Review', '')
        metadata['gtin'] = root.findtext('GTIN', '')

        # Extraire les entrées <Page> si présentes
        pages_elem = root.find('Pages')
        if pages_elem is not None:
            pages = []
            for page in pages_elem:
                attribs = {k: v for k, v in page.attrib.items()}
                if attribs:
                    pages.append(attribs)
            if pages:
                metadata['pages'] = pages

        return metadata
    except Exception:
        return None


COMIC_INFO_NAME = "ComicInfo.xml"


def is_comic_info_name(name):
    """Nom exactement ComicInfo.xml, à la racine (casse ignorée, comme les
    lecteurs de BD) : le ComicInfo.xml d'une BD quand il existe
    (comic_info_member_index), et le nom qu'un fichier arrivant dans un
    document ne peut prendre que si celui-ci n'a pas encore le sien
    (name_incoming_comic_infos)."""
    return (name or "").replace("\\", "/").lower() == "comicinfo.xml"


def is_comic_info_entry(entry):
    """Entrée nommée ComicInfo.xml à la racine (is_comic_info_name). Le
    ComicInfo.xml d'un document se cherche par comic_info_entry, qui reconnaît
    aussi celui du dossier qui contient toutes les pages."""
    return not entry.get("is_dir") and is_comic_info_name(entry.get("orig_name", ""))


def comic_info_member_index(names):
    """Position, dans la liste de noms d'une archive ou d'un document, du
    ComicInfo.xml de la BD ; None s'il n'en a pas. Le premier nommé exactement
    ComicInfo.xml à la racine (name_incoming_comic_infos renomme les
    suivants) ; à défaut, celui du dossier qui contient toutes les pages —
    une archive faite en compressant un dossier range ses métadonnées avec
    ses pages. Ce second cas exige un seul ComicInfo.xml rangé dans un
    dossier et toutes les images dans ce dossier (sous-dossiers compris) :
    sinon il pourrait s'agir des métadonnées d'un autre tome, et ces fichiers
    restent des annexes. Les fichiers de Finder (__MACOSX/) ne comptent pas.
    Casse du dossier respectée, comme les noms d'une archive."""
    from modules.qt.archive_names import IMAGE_EXTS
    paths = [(n or "").replace("\\", "/") for n in names]
    for i, path in enumerate(paths):
        if path.lower() == "comicinfo.xml":
            return i
    candidates = [i for i, path in enumerate(paths)
                  if "/" in path and path.rsplit("/", 1)[1].lower() == "comicinfo.xml"
                  and not path.startswith("__MACOSX/")]
    if len(candidates) != 1:
        return None
    folder = paths[candidates[0]].rsplit("/", 1)[0] + "/"
    images = [path for path in paths
              if not path.endswith("/") and not path.startswith("__MACOSX/")
              and os.path.splitext(path)[1].lower() in IMAGE_EXTS]
    if images and all(path.startswith(folder) for path in images):
        return candidates[0]
    return None


def comic_info_entry_in(entries):
    """ComicInfo.xml (comic_info_member_index) parmi ces entrées, None s'il
    n'y en a pas. Les dossiers ne comptent pas."""
    files = [e for e in entries if not e.get("is_dir")]
    index = comic_info_member_index([e.get("orig_name", "") for e in files])
    return None if index is None else files[index]


def comic_info_entry(state):
    """Entrée du ComicInfo.xml du document (comic_info_entry_in), None s'il
    n'en a pas."""
    return comic_info_entry_in(state.images_data)


def name_incoming_comic_infos(new_entries, existing_entries):
    """Fichiers ComicInfo.xml qui arrivent dans un document (ouverture, ajout
    d'archive, dépôt ou collage de fichiers, glisser-déposer entre panneaux) :
    le premier garde le nom ComicInfo.xml si le document n'en a pas — il
    devient ses métadonnées —, les autres sont renommés ComicInfo_0002.xml,
    _0003… (casse d'origine conservée, noms déjà pris sautés) et restent des
    fichiers annexes. Un document n'a ainsi jamais qu'un seul ComicInfo.xml ;
    le sien peut aussi être celui du dossier de ses pages
    (comic_info_member_index), qu'un ComicInfo.xml arrivé à la racine ne
    remplace donc pas. Renomme les entrées de new_entries sur place."""
    taken = {e.get("orig_name", "").lower() for e in existing_entries}
    has_main = comic_info_entry_in(existing_entries) is not None
    for entry in new_entries:
        if not is_comic_info_entry(entry):
            continue
        if not has_main:
            has_main = True
            continue
        entry["orig_name"] = annex_comic_info_name(entry["orig_name"], taken)
        if "name" in entry:
            entry["name"] = entry["orig_name"]
        taken.add(entry["orig_name"].lower())


def annex_comic_info_name(name, taken):
    """Nom d'annexe libre pour un fichier ComicInfo.xml : ComicInfo_0002.xml,
    _0003… (casse d'origine conservée). taken : noms déjà pris, en
    minuscules."""
    base, ext = os.path.splitext(name)
    n = 2
    while f"{base}_{n:04d}{ext}".lower() in taken:
        n += 1
    return f"{base}_{n:04d}{ext}"


def replace_comic_info_bytes(state, entry, new_bytes):
    """Remplace le contenu du ComicInfo.xml `entry` du document. Si l'ancien
    contenu n'était lisible que réparé (comic_info_strictly_readable faux),
    il est d'abord gardé intact dans le document, juste après, sous un nom
    d'annexe libre (annex_comic_info_name, noms du document et, pour le lot
    d'import de métadonnées, ceux de l'archive : state._archive_member_names) :
    la partie illisible n'est jamais perdue. Le nom de cette copie est noté
    dans state.comic_info_backup_name, et comic_info_backup_signal émis
    (fenêtre d'information du panneau). Rend ce nom, None sans copie. La copie
    est mise en place avant le remplacement : un échec en la créant laisse
    l'original à sa place (les appelants qui avalent leurs erreurs n'ont
    alors rien perdu)."""
    old = entry.get("bytes")
    if comic_info_strictly_readable(old) or _as_bytes(old) == _as_bytes(new_bytes):
        entry["bytes"] = new_bytes
        return None
    taken = {e.get("orig_name", "").lower() for e in state.images_data}
    taken |= {n.lower() for n in getattr(state, "_archive_member_names", ())}
    name = annex_comic_info_name(entry.get("orig_name") or COMIC_INFO_NAME, taken)
    from modules.qt.entries import create_entry
    backup = create_entry(name, _as_bytes(old), ())
    backup["name"] = name
    if entry.get("source_archive") is not None:
        backup["source_archive"] = entry["source_archive"]
    try:
        position = next(i for i, e in enumerate(state.images_data) if e is entry) + 1
    except StopIteration:
        position = len(state.images_data)
    state.images_data.insert(position, backup)
    entry["bytes"] = new_bytes
    state.comic_info_backup_name = name
    state.modified = True
    try:
        from modules.qt.metadata_signal import comic_info_backup_signal
        comic_info_backup_signal.emit(state)
    except ImportError:
        pass  # repli : Qt absent (tests), la copie est faite, seul l'avertissement manque
    return name


def repair_comic_info_on_open(state):
    """À l'ouverture d'une BD dans un panneau : un ComicInfo.xml lisible
    seulement réparé (comic_info_repaired) est aussitôt remplacé par sa
    version réparée, et l'original gardé en annexe (replace_comic_info_bytes,
    qui émet comic_info_backup_signal ; state.comic_info_repaired_on_open
    vrai pendant cette émission, pour le texte de la fenêtre). L'utilisateur
    l'apprend en une fois, au moment où il ouvre la BD, au lieu d'un bandeau
    puis d'une copie faite plus tard par une autre commande. Document marqué
    modifié : rien n'est écrit sur le disque avant l'enregistrement.
    L'appelant pose un état d'historique avant et après (Annuler rend le
    fichier d'origine). Rend True si la réparation a été faite ; False si
    rien n'était à réparer, ou si la version réparée ne se réécrit pas (le
    fichier reste alors tel quel, signalé par le bandeau de l'onglet)."""
    if not comic_info_repaired(state):
        return False
    entry = comic_info_entry(state)
    try:
        root, source = _comic_tree(entry["bytes"])
        new_bytes = _serialize_comic_xml(root, source)
    except Exception:
        return False
    state.comic_info_repaired_on_open = True
    try:
        replace_comic_info_bytes(state, entry, new_bytes)
    finally:
        state.comic_info_repaired_on_open = False
    sync_comic_metadata(state, force=True)
    return True


def comic_info_unreadable(state):
    """Le document a un ComicInfo.xml (comic_info_entry), mais aucune
    métadonnée n'a pu en être lue (XML mal formé, refusé par le parseur durci,
    membre d'archive illisible ou fichier vide). Déduit de l'état, sans
    drapeau à tenir à jour : suit Annuler, les renommages et les déplacements
    entre panneaux comme sync_comic_metadata."""
    return not state.comic_metadata and comic_info_entry(state) is not None


def comic_info_repaired(state):
    """Les métadonnées affichées ont été lues dans une version réparée du
    ComicInfo.xml (_repaired_comic_xml) : le fichier du document est
    abîmé ou refusé par le parseur durci. Déduit de l'état, comme
    comic_info_unreadable."""
    if not state.comic_metadata:
        return False
    entry = comic_info_entry(state)
    return entry is not None and not comic_info_strictly_readable(entry.get("bytes"))


def shows_metadata_tab(state):
    """L'onglet Métadonnées est affiché : métadonnées lues, ou ComicInfo.xml
    illisible (l'onglet l'explique au lieu de disparaître sans raison)."""
    return bool(state.comic_metadata) or comic_info_unreadable(state)


def sync_comic_metadata(state, force=False):
    """Aligne state.comic_metadata (copie analysée) sur le ComicInfo.xml du
    document : relu quand ce fichier change (arrivée, renommage d'un fichier
    en ComicInfo.xml, Annuler…), effacé quand il n'y en a plus. Le fichier
    suivi est mémorisé par son jeton d'historique (undo_redo.entry_key), qui
    survit à une modification de son contenu : les éditeurs qui modifient le
    ComicInfo.xml mettent eux-mêmes les métadonnées à jour, ou passent
    force=True pour une relecture. Retourne True si les métadonnées ont changé."""
    from modules.qt.undo_redo import entry_key
    entry = comic_info_entry(state)
    if entry is None:
        state._comic_info_key = None
        if state.comic_metadata:
            state.comic_metadata = None
            state._page_attrs_by_entry_id = {}
            return True
        return False
    key = entry_key(entry)
    if not force and key == getattr(state, "_comic_info_key", None):
        return False
    state._comic_info_key = key
    data = entry.get("bytes")
    state.comic_metadata = parse_comic_info_xml(data) if data else None
    if state.comic_metadata and state.comic_metadata.get("page_count"):
        try:
            state.original_page_count = int(state.comic_metadata["page_count"])
        except (ValueError, TypeError):
            pass
    build_page_attrs_map(state)
    return True


def comic_info_member(members, name_of):
    """Membre d'archive (ZipInfo, RarInfo, TarInfo…) qui est le ComicInfo.xml
    de la BD (comic_info_member_index), None s'il n'y en a pas. members :
    fichiers de l'archive, dossiers exclus ; name_of(membre) -> nom."""
    members = list(members)
    index = comic_info_member_index([name_of(m) for m in members])
    return None if index is None else members[index]


def read_comic_info(filepath):
    """Retourne un dictionnaire de métadonnées, ou None si l'archive
    (CBZ/CBR/CBT) n'a pas de ComicInfo.xml (comic_info_member : à la racine,
    ou dans le dossier de ses pages). Mêmes règles qu'à l'ouverture du
    comic : ZIP ouvert par open_zip (noms écrits sans le drapeau UTF-8 relus
    dans leur vrai codage), nom TAR sans « ./ » (tar_entry_name), membres lus
    par leur objet d'archive et jamais par nom (homonymes), limite de taille
    des lecteurs communs."""
    from modules.qt.archive_loader import _check_member_size, _tar_info_read
    from modules.qt.archive_names import open_zip, tar_entry_name
    try:
        ext = os.path.splitext(filepath)[1].lower()

        if ext == '.cbz':
            with open_zip(filepath) as archive:
                info = comic_info_member((i for i in archive.infolist() if not i.is_dir()),
                                         lambda i: i.filename)
                if info is not None:
                    _check_member_size(info.file_size)
                    return parse_comic_info_xml(archive.read(info))

        elif ext == '.cbr' and rarfile is not None:
            with rarfile.RarFile(filepath, 'r') as archive:
                info = comic_info_member((i for i in archive.infolist() if not i.is_dir()),
                                         lambda i: i.filename)
                if info is not None:
                    _check_member_size(info.file_size)
                    return parse_comic_info_xml(archive.read(info))

        elif ext == '.cbt':
            with tarfile.open(filepath, 'r:*') as archive:
                member = comic_info_member((m for m in archive.getmembers() if m.isfile()),
                                           lambda m: tar_entry_name(m.name))
                if member is not None:
                    return parse_comic_info_xml(_tar_info_read(archive, member))

    except Exception:
        return None

    return None


_TRACE_NOTE_PREFIX = "MosaicView: metadata retrieved on"


def _update_trace_note(notes_text, date_str):
    """
    Ajoute ou remplace, dans le texte du champ Notes, la ligne de traçabilité
    MosaicView ("MosaicView: metadata retrieved on {date}."). Le reste du
    contenu de Notes (texte utilisateur ou d'un autre outil) est préservé
    intact — seule la ligne portant le préfixe MosaicView est remplacée.
    """
    new_line = f"{_TRACE_NOTE_PREFIX} {date_str}."
    notes_text = notes_text or ""
    lines = notes_text.split("\n") if notes_text else []
    for i, line in enumerate(lines):
        if line.startswith(_TRACE_NOTE_PREFIX):
            lines[i] = new_line
            return "\n".join(lines)
    if notes_text:
        return notes_text + "\n" + new_line
    return new_line


_SCRAPER_FIELD_MAP = {
    "title": "Title", "series": "Series", "number": "Number",
    "volume": "Volume", "summary": "Summary", "writer": "Writer",
    "penciller": "Penciller", "inker": "Inker", "colorist": "Colorist",
    "letterer": "Letterer", "cover_artist": "CoverArtist",
    "editor": "Editor", "publisher": "Publisher", "imprint": "Imprint",
    "genre": "Genre", "web": "Web", "year": "Year", "month": "Month",
    "day": "Day", "characters": "Characters", "teams": "Teams",
    "locations": "Locations", "story_arc": "StoryArc",
}


def write_comic_metadata_from_scraper(state, meta):
    """
    Écrit dans state.images_data (ComicInfo.xml) les métadonnées `meta` obtenues
    depuis le scraper ComicVine (get_issue_details). Met à jour state.comic_metadata,
    state.modified et régénère <Pages> si absent. Émet metadata_signal.
    """
    from modules.qt.metadata_signal import metadata_signal

    st = state
    if not st:
        return

    xml_entry = comic_info_entry(st)

    if xml_entry and xml_entry.get("bytes"):
        # Fichier abîmé ou refusé par le parseur durci : sa version réparée
        # (_comic_tree), complétée ci-dessous ; l'original est gardé en annexe
        # par replace_comic_info_bytes. Irrécupérable : ComicInfo neuf, original
        # gardé de même.
        try:
            root, original_bytes = _comic_tree(xml_entry["bytes"])
        except Exception:
            root = ET.Element("ComicInfo")
            original_bytes = None
    else:
        root = ET.Element("ComicInfo")
        original_bytes = None

    for field, tag in _SCRAPER_FIELD_MAP.items():
        value = meta.get(field, "").strip()
        if not value:
            continue
        elem = root.find(tag)
        if elem is None:
            elem = ET.SubElement(root, tag)
        elem.text = value

    web_url = meta.get("web", "").strip()
    if web_url:
        from datetime import datetime
        notes_elem = root.find("Notes")
        if notes_elem is None:
            notes_elem = ET.SubElement(root, "Notes")
        notes_elem.text = _update_trace_note(
            notes_elem.text, datetime.now().strftime("%Y-%m-%d"))

    new_bytes = _serialize_comic_xml(root, original_bytes)

    if xml_entry:
        replace_comic_info_bytes(st, xml_entry, new_bytes)
    else:
        st.images_data.append({
            "orig_name": "ComicInfo.xml", "name": "ComicInfo.xml",
            "bytes": new_bytes, "is_image": False,
            "is_dir": False, "extension": ".xml",
        })

    sync_comic_metadata(st, force=True)
    st.modified = True

    # Si le XML n'avait pas de <Pages>, les construire depuis images_data
    if st.comic_metadata and 'pages' not in st.comic_metadata:
        # Injecter <Pages> vide dans le XML pour que sync_pages_in_xml_data puisse le remplir
        xml_entry_now = comic_info_entry(st)
        if xml_entry_now and xml_entry_now.get('bytes'):
            try:
                root_now = _comic_fromstring(xml_entry_now['bytes'])
                ET.SubElement(root_now, 'Pages')
                replace_comic_info_bytes(st, xml_entry_now,
                                         _serialize_comic_xml(root_now, xml_entry_now['bytes']))
                st.comic_metadata['pages'] = []
                sync_pages_in_xml_data(st, emit_signal=False)
            except Exception:
                pass  # repli : ComicInfo.xml gardé sans <Pages>, balise facultative

    metadata_signal.emit(st)


# Sous-ensemble de _SCRAPER_FIELD_MAP réellement comparable : "imprint" est
# dans FIELD_MAP mais get_issue_details() ne le renseigne jamais, une diff
# dessus serait donc toujours un faux "ajouté" côté distant.
_DIFF_FIELDS = [k for k in _SCRAPER_FIELD_MAP if k != "imprint"]


def diff_comic_metadata(local_meta, remote_meta):
    """
    Compare les métadonnées locales (state.comic_metadata) aux métadonnées
    fraîchement téléchargées depuis ComicVine (get_issue_details), champ par
    champ, sur le sous-ensemble de champs que le scraper renseigne réellement.

    Retourne une liste de (field_key, status), status ∈ {"modified", "added"} :
    - "added"    : vide en local, non vide côté distant.
    - "modified" : non vide des deux côtés mais différent.
    Un champ vide côté distant n'est jamais reporté (on ne supprime jamais une
    donnée locale silencieusement).
    """
    local_meta = local_meta or {}
    remote_meta = remote_meta or {}
    diffs = []
    for key in _DIFF_FIELDS:
        remote_val = str(remote_meta.get(key, "") or "").strip()
        if not remote_val:
            continue
        local_val = str(local_meta.get(key, "") or "").strip()
        if not local_val:
            diffs.append((key, "added"))
        elif local_val != remote_val:
            diffs.append((key, "modified"))
    return diffs


def get_source_comicvine_issue_id(comic_metadata):
    """
    Détecte une URL ComicVine d'issue dans le champ standard ComicInfo Web.

    Retourne l'id numérique de l'issue (str) si trouvé, sinon None. Ignore les
    URLs de série (4050-xxxxx) : seule une URL d'issue précise est exploitable
    pour retélécharger des métadonnées de numéro.
    """
    if not comic_metadata:
        return None
    from modules.qt.comicvine_url_dialog_qt import _parse_comicvine_url

    url = (comic_metadata.get("web") or "").strip()
    if not url:
        return None
    parsed = _parse_comicvine_url(url)
    if parsed and parsed[0] == "issue":
        return parsed[1]
    return None


def get_current_image_count(state):
    """
    Retourne le nombre actuel d'images (pages) dans l'archive,
    en excluant les répertoires, le fichier ComicInfo.xml et les fichiers non-images
    """
    count = 0
    for entry in state.images_data:
        if entry.get("is_image") and not entry.get("is_dir") and not is_comic_info_entry(entry):
            count += 1
    return count


def has_comic_info_entry(state):
    return comic_info_entry(state) is not None


def update_page_count_in_xml_data(state, new_page_count):
    """
    Met à jour le nombre de pages dans le fichier ComicInfo.xml (données uniquement).
    Modifie les bytes XML dans images_data et met à jour state.comic_metadata.
    Retourne True si la mise à jour a réussi, False sinon.
    N'appelle aucune fonction UI — le code appelant doit gérer les mises à jour visuelles.
    """
    if not state.images_data or not state.comic_metadata:
        return False

    try:
        xml_entry = comic_info_entry(state)

        if not xml_entry or not xml_entry.get('bytes'):
            return False

        root, _source_bytes = _comic_tree(xml_entry['bytes'])

        page_count_elem = root.find('PageCount')
        if page_count_elem is not None:
            page_count_elem.text = str(new_page_count)
        else:
            page_count_elem = ET.SubElement(root, 'PageCount')
            page_count_elem.text = str(new_page_count)

        xml_string = _serialize_comic_xml(root, _source_bytes)
        replace_comic_info_bytes(state, xml_entry, xml_string)

        state.comic_metadata['page_count'] = str(new_page_count)
        state.original_page_count = new_page_count
        state.modified = True

        return True

    except Exception:
        return False


_PAGE_FILE_ATTRS = ("ImageWidth", "ImageHeight", "ImageSize")


def _page_file_attrs(entry):
    """Attributs de <Page> qui décrivent le fichier de la page (dimensions déjà
    mémorisées si possible, sinon lues). Lève une exception si l'image est illisible."""
    w = entry.get('img_width')
    h = entry.get('img_height')
    if not w or not h:
        from modules.qt.entries import page_size
        w, h = page_size(entry['bytes'])
    return {'ImageWidth': str(w), 'ImageHeight': str(h), 'ImageSize': str(len(entry['bytes']))}


def transfer_page_attrs(state, source, target, without=()):
    """target remplace source dans le document : elle reprend les attributs de
    <Page> de source (type, double page, signet, attributs d'autres logiciels),
    sauf ceux qui décrivent le fichier, qui sont les siens, et ceux de `without`.
    Rangés par page, ces attributs disparaîtraient sinon avec source. À appeler
    avant sync_pages_in_xml_data."""
    attrs_by_id = getattr(state, '_page_attrs_by_entry_id', {})
    attrs = {k: v for k, v in attrs_by_id.get(id(source), {}).items()
             if k not in _PAGE_FILE_ATTRS and k not in without}
    if not attrs:
        return
    try:
        attrs.update(_page_file_attrs(target))
    except Exception:
        pass  # repli : <Page> écrite sans dimensions
    attrs_by_id[id(target)] = attrs


# Types de page de la norme ComicInfo (attribut Type de <Page>), dans l'ordre
# des menus. Une seule valeur par page : la documentation de la norme, ComicRack
# et les lecteurs (couverture reconnue par Type="FrontCover") la traitent ainsi,
# même si le schéma admet une liste séparée par des espaces.
PAGE_TYPES = ("FrontCover", "InnerCover", "Story", "Roundup", "Editorial",
              "Letters", "Advertisement", "Preview", "BackCover", "Other", "Deleted")

_KEEP = object()


def page_entries(state):
    """Pages du document dans l'ordre : celles que décrit <Pages>
    (<Page Image="N"> = position dans cette liste)."""
    return [e for e in state.images_data
            if e.get('is_image') and not e.get('is_dir') and not is_comic_info_entry(e)]


def page_entries_at(state, image_indices):
    """Pages aux positions <Page Image="N"> données (hors limites ignorées)."""
    pages = page_entries(state)
    return [pages[i] for i in sorted(set(image_indices)) if 0 <= i < len(pages)]


def selected_page_entries(state):
    """Pages sélectionnées dans la mosaïque (dossiers, ComicInfo.xml et
    fichiers annexes exclus)."""
    page_ids = {id(e) for e in page_entries(state)}
    return [state.images_data[i] for i in sorted(state.selected_indices)
            if i < len(state.images_data) and id(state.images_data[i]) in page_ids]


def page_types_editable(state):
    """Le type des pages peut être posé : le document a un ComicInfo.xml lu."""
    return bool(state.comic_metadata) and comic_info_entry(state) is not None


def is_double_page(attrs):
    return str(attrs.get('DoublePage', '')).strip().lower() in ('true', '1')


def page_type_of(state, entry):
    """Attribut Type de la <Page> de `entry` tel qu'écrit ("" s'il n'y en a pas)."""
    attrs = getattr(state, '_page_attrs_by_entry_id', {}).get(id(entry), {})
    return str(attrs.get('Type', '')).strip()


def is_deleted_page(state, entry):
    """La page est marquée « Supprimée » (Type="Deleted") : les lecteurs de BD
    ne doivent pas l'afficher. Elle reste dans le fichier ; la visionneuse la
    saute seulement si le réglage est actif (ImageViewer.get_reading_indices)."""
    return "Deleted" in page_type_of(state, entry).split()


def page_marks_summary(state, entries):
    """État des pages `entries` pour le menu « Type de page » :
    ({type: nombre de pages qui l'ont} pour chaque type porté par au moins
     une page, nombre de doubles pages). Toutes les pages l'ont quand le
    nombre vaut len(entries)."""
    attrs_by_id = getattr(state, '_page_attrs_by_entry_id', {})
    counts = {}
    doubles = 0
    for entry in entries:
        attrs = attrs_by_id.get(id(entry), {})
        for page_type in set(str(attrs.get('Type', '')).split()):
            counts[page_type] = counts.get(page_type, 0) + 1
        if is_double_page(attrs):
            doubles += 1
    return counts, doubles


def set_page_marks(state, entries, page_type=_KEEP, double_page=_KEEP):
    """Pose le type (page_type : valeur de PAGE_TYPES, '' ou None pour le
    retirer) et/ou la double page (True / False) de chaque page de `entries`,
    sans toucher à leurs autres attributs ni aux autres pages. Crée <Pages>
    si le ComicInfo.xml n'en a pas. Rend True si le document a changé, False
    si aucune page ne change. Ni point d'historique ni signal : à l'appelant."""
    xml_entry = comic_info_entry(state)
    if not entries or not state.comic_metadata or xml_entry is None or not xml_entry.get('bytes'):
        return False
    pages = page_entries(state)
    page_ids = {id(e) for e in pages}
    old_by_id = getattr(state, '_page_attrs_by_entry_id', {})
    attrs_by_id = dict(old_by_id)
    changed = False
    for entry in entries:
        if id(entry) not in page_ids:
            continue
        old = old_by_id.get(id(entry), {})
        attrs = dict(old)
        if page_type is not _KEEP:
            if page_type:
                attrs['Type'] = page_type
            else:
                attrs.pop('Type', None)
        if double_page is not _KEEP and bool(double_page) != is_double_page(attrs):
            if double_page:
                attrs['DoublePage'] = 'true'
            else:
                attrs.pop('DoublePage', None)
        if attrs == old:
            continue
        if not old and entry.get('bytes'):
            # Page encore sans <Page> : décrite comme par sync_pages_in_xml_data
            try:
                attrs.update(_page_file_attrs(entry))
            except Exception:
                pass  # repli : <Page> écrite sans dimensions
        attrs_by_id[id(entry)] = attrs
        changed = True
    if not changed:
        return False

    root, source_bytes = _comic_tree(xml_entry['bytes'])
    pages_elem = root.find('Pages')
    if pages_elem is None:
        pages_elem = ET.SubElement(root, 'Pages')
    new_pages_meta, new_attrs_by_id = _rewrite_page_elements(pages_elem, pages, attrs_by_id)
    page_count_elem = root.find('PageCount')
    if page_count_elem is None:
        page_count_elem = ET.SubElement(root, 'PageCount')
    page_count_elem.text = str(len(pages))
    xml_string = _serialize_comic_xml(root, source_bytes)

    replace_comic_info_bytes(state, xml_entry, xml_string)
    state.comic_metadata['pages'] = new_pages_meta
    state.comic_metadata['page_count'] = str(len(pages))
    state._page_attrs_by_entry_id = new_attrs_by_id
    state.original_page_count = len(pages)
    state.modified = True
    return True


def _rewrite_page_elements(pages_elem, real_entries, attrs_by_id):
    """Remplace les <Page> de pages_elem par une par entrée, dans l'ordre de
    real_entries, avec ses attributs (attrs_by_id, non modifié). Rend
    (métadonnées des pages, attributs par id d'entrée)."""
    for page in list(pages_elem):
        pages_elem.remove(page)

    new_pages_meta = []
    new_attrs_by_id = {}
    for new_idx, entry in enumerate(real_entries):
        attrs = dict(attrs_by_id.get(id(entry), {}))
        if not attrs and entry.get('bytes'):
            try:
                attrs.update(_page_file_attrs(entry))
            except Exception:
                pass  # repli : <Page> écrite sans dimensions
        else:
            # Complète ImageSize si absent du XML d'origine
            if 'ImageSize' not in attrs and entry.get('bytes'):
                attrs['ImageSize'] = str(len(entry['bytes']))
        new_page = ET.SubElement(pages_elem, 'Page')
        new_page.set('Image', str(new_idx))
        for k, v in attrs.items():
            new_page.set(k, v)
        new_meta = {'Image': str(new_idx)}
        new_meta.update(attrs)
        new_pages_meta.append(new_meta)
        new_attrs_by_id[id(entry)] = attrs
    return new_pages_meta, new_attrs_by_id


def _pages_describe(pages_meta, ordered_entries, attrs_by_id):
    """<Pages> (pages_meta) décrit déjà chaque page à sa position dans
    ordered_entries. Une liste partielle (pages sans <Page>) ou une <Page>
    au-delà de la dernière page d'un fichier reçu tel quel ne compte pas."""
    described = {}
    for meta in pages_meta:
        try:
            described[int(meta.get('Image', -1))] = {k: v for k, v in meta.items() if k != 'Image'}
        except (ValueError, TypeError):
            continue
    for idx, entry in enumerate(ordered_entries):
        attrs = attrs_by_id.get(id(entry), {})
        if idx in described:
            if attrs != described[idx]:
                return False
        elif attrs:
            return False
    return True


def comic_info_in_name_order(state, members, name_key):
    """Contenu à écrire pour le ComicInfo.xml du document dans un CBZ dont les
    membres sont members ([(nom, entrée)], utils.cbz_members) : <Page Image="N">
    y désigne la N-ième page dans l'ordre des noms, name_key étant la clé de tri
    de la réouverture. L'ordre affiché peut en différer (renumérotation
    désactivée après un tri, un glisser-déposer) : la réouverture retrie, et
    chaque information passerait sur une autre page. None : à écrire tel quel.
    Ne modifie ni le document ni les données de la page en mémoire, qui décrivent
    l'ordre affiché."""
    metadata = state.comic_metadata
    if not metadata or 'pages' not in metadata:
        return None
    xml_entry = comic_info_entry(state)
    if not xml_entry or not xml_entry.get('bytes'):
        return None
    pages = [(name, e) for name, e in members
             if e.get('is_image') and not is_comic_info_entry(e)]
    ordered = [e for _name, e in sorted(pages, key=lambda member: name_key(member[0]))]
    attrs_by_id = getattr(state, '_page_attrs_by_entry_id', {})
    if _pages_describe(metadata['pages'], ordered, attrs_by_id):
        return None
    try:
        root, source_bytes = _comic_tree(xml_entry['bytes'])
        pages_elem = root.find('Pages')
        if pages_elem is None:
            return None
        _rewrite_page_elements(pages_elem, ordered, attrs_by_id)
        return _serialize_comic_xml(root, source_bytes)
    except Exception:
        return None  # repli : ComicInfo.xml écrit tel quel, comme un <Pages> non resynchronisé


def comic_info_for_selection(state, members, name_key):
    """Contenu à écrire pour le ComicInfo.xml du document dans un CBZ qui ne
    contient qu'une partie des pages (Enregistrer la sélection) : PageCount et
    <Pages> ne décrivent que ces pages, chacune avec ses attributs, dans l'ordre
    des noms (name_key : clé de tri de la réouverture). Les autres champs décrivent
    toujours la même BD. Ne modifie pas le document. Une copie renommée porte
    l'id de sa page dans "_selection_source_id". None : ComicInfo.xml absent des
    members, ou illisible, à écrire tel quel."""
    xml_entry = comic_info_entry(state)
    if not xml_entry or not xml_entry.get('bytes') or not state.comic_metadata:
        return None
    if not any(entry is xml_entry for _name, entry in members):
        return None
    pages = [(name, e) for name, e in members if e.get('is_image') and not is_comic_info_entry(e)]
    ordered = [e for _name, e in sorted(pages, key=lambda member: name_key(member[0]))]
    known = getattr(state, '_page_attrs_by_entry_id', {})
    attrs_by_id = {id(e): known.get(e.get('_selection_source_id', id(e)), {}) for e in ordered}
    try:
        root, source_bytes = _comic_tree(xml_entry['bytes'])
        page_count = root.find('PageCount')
        if page_count is None:
            page_count = ET.SubElement(root, 'PageCount')
        page_count.text = str(len(ordered))
        pages_elem = root.find('Pages')
        if pages_elem is not None:
            _rewrite_page_elements(pages_elem, ordered, attrs_by_id)
        return _serialize_comic_xml(root, source_bytes)
    except Exception:
        return None  # repli : ComicInfo.xml écrit tel quel


def sync_pages_in_xml_data(state, emit_signal=True):
    """
    Resynchronise la section <Pages> du ComicInfo.xml avec l'état courant de images_data.

    Gère tous les cas : réordonnancement, ajout d'images, suppression d'images.
    - Les entrées connues (dans _page_attrs_by_entry_id) récupèrent leurs attributs.
    - Les nouvelles entrées obtiennent une <Page Image="N"> sans attributs supplémentaires.
    - Les entrées supprimées disparaissent naturellement.

    Guard : ne fait rien si 'pages' absent de comic_metadata
    (= pas de <Pages> dans le XML original).
    """
    if not state.images_data or not state.comic_metadata:
        return
    if 'pages' not in state.comic_metadata:
        return

    try:
        xml_entry = comic_info_entry(state)
        if not xml_entry or not xml_entry.get('bytes'):
            return

        root, _source_bytes = _comic_tree(xml_entry['bytes'])
        pages_elem = root.find('Pages')
        if pages_elem is None:
            return

        attrs_by_id = getattr(state, '_page_attrs_by_entry_id', {})

        real_entries = [
            e for e in state.images_data
            if e.get('is_image') and not e.get('is_dir') and not is_comic_info_entry(e)
        ]

        new_pages_meta, new_attrs_by_id = _rewrite_page_elements(pages_elem, real_entries,
                                                                 attrs_by_id)

        state.comic_metadata['pages'] = new_pages_meta
        state._page_attrs_by_entry_id = new_attrs_by_id

        new_count = len(real_entries)
        page_count_elem = root.find('PageCount')
        if page_count_elem is not None:
            page_count_elem.text = str(new_count)
        else:
            ET.SubElement(root, 'PageCount').text = str(new_count)
        state.comic_metadata['page_count'] = str(new_count)
        state.original_page_count = new_count

        xml_string = _serialize_comic_xml(root, _source_bytes)
        replace_comic_info_bytes(state, xml_entry, xml_string)
        state.modified = True

        if emit_signal:
            from modules.qt.metadata_signal import metadata_signal
            metadata_signal.emit(state)

    except Exception:
        pass  # repli : XML inchangé (remplacé seulement à la fin), <Pages> non resynchronisé


def build_page_attrs_map(state):
    """
    Construit state._page_attrs_by_entry_id : {id(entry): {attr: val}}
    à partir de state.comic_metadata['pages'] et de l'ordre courant de images_data.
    À appeler après chaque assignation de state.comic_metadata (chargement, undo/redo).
    """
    state._page_attrs_by_entry_id = {}
    if not state.comic_metadata or 'pages' not in state.comic_metadata:
        return
    real_entries = [
        e for e in state.images_data
        if e.get('is_image') and not e.get('is_dir') and not is_comic_info_entry(e)
    ]
    for meta in state.comic_metadata['pages']:
        try:
            idx = int(meta.get('Image', -1))
        except (ValueError, TypeError):
            continue
        if 0 <= idx < len(real_entries):
            attrs = {k: v for k, v in meta.items() if k != 'Image'}
            state._page_attrs_by_entry_id[id(real_entries[idx])] = attrs


def get_page_image_index(state, entry):
    """
    Retourne l'index <Page Image="N"> d'une entrée dans images_data.
    Cet index = position parmi les images réelles (hors dossiers et hors ComicInfo.xml),
    dans l'ordre de images_data.
    Retourne None si l'entrée n'est pas une image réelle.
    """
    idx = 0
    for e in state.images_data:
        if e.get('is_dir') or is_comic_info_entry(e):
            continue
        if not e.get('is_image'):
            continue
        if e is entry:
            return idx
        idx += 1
    return None


def update_page_entries_in_xml_data(state, entries_with_idx, emit_signal=True):
    """
    Met à jour les attributs ImageSize, ImageWidth, ImageHeight des éléments <Page>
    dans ComicInfo.xml pour les entrées modifiées.

    entries_with_idx : liste de (page_image_index, entry)
        - page_image_index : int, valeur de l'attribut Image="N" dans <Page>
        - entry : dict images_data avec entry['bytes'] déjà mis à jour
    emit_signal : si False, n'émet pas metadata_signal (utile depuis un thread worker)

    Guard : ne fait rien si :
    - pas de comic_metadata
    - pas de clé 'pages' dans comic_metadata (= pas de <Pages> dans le XML original)
    - <Pages> présente mais aucune <Page> n'a d'attributs au-delà de Image=
    """
    if not state.images_data or not state.comic_metadata:
        return
    if 'pages' not in state.comic_metadata:
        return

    try:
        xml_entry = comic_info_entry(state)
        if not xml_entry or not xml_entry.get('bytes'):
            return

        root, _source_bytes = _comic_tree(xml_entry['bytes'])
        pages_elem = root.find('Pages')
        if pages_elem is None:
            return

        page_by_idx = {}
        for page in pages_elem:
            img_attr = page.get('Image')
            if img_attr is not None:
                page_by_idx[int(img_attr)] = page

        changed = False
        for page_image_index, entry in entries_with_idx:
            page_elem = page_by_idx.get(page_image_index)
            if page_elem is None:
                continue
            try:
                from modules.qt.entries import page_size
                w, h = page_size(entry['bytes'])
            except Exception:
                continue
            new_size = len(entry['bytes'])
            page_elem.set('ImageWidth', str(w))
            page_elem.set('ImageHeight', str(h))
            page_elem.set('ImageSize', str(new_size))
            for p in state.comic_metadata['pages']:
                if p.get('Image') == str(page_image_index):
                    p['ImageWidth'] = str(w)
                    p['ImageHeight'] = str(h)
                    p['ImageSize'] = str(new_size)
                    break
            attrs_by_id = getattr(state, '_page_attrs_by_entry_id', {})
            entry_attrs = attrs_by_id.get(id(entry), {})
            entry_attrs['ImageWidth'] = str(w)
            entry_attrs['ImageHeight'] = str(h)
            entry_attrs['ImageSize'] = str(new_size)
            attrs_by_id[id(entry)] = entry_attrs
            state._page_attrs_by_entry_id = attrs_by_id
            changed = True

        if changed:
            xml_string = _serialize_comic_xml(root, _source_bytes)
            replace_comic_info_bytes(state, xml_entry, xml_string)
            state.modified = True
            if emit_signal:
                from modules.qt.metadata_signal import metadata_pages_signal
                metadata_pages_signal.emit(state)

    except Exception:
        pass  # repli : XML inchangé (remplacé seulement à la fin), anciennes dimensions gardées
