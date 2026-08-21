# -------------------------
# Parsing et gestion des métadonnées ComicInfo.xml
# -------------------------
import os
import tarfile
import zipfile
import xml.etree.ElementTree as ET
try:
    from defusedxml.ElementTree import fromstring as _safe_fromstring
except ImportError:
    _safe_fromstring = ET.fromstring

try:
    import rarfile
except ImportError:
    rarfile = None


def _serialize_comic_xml(root, original_bytes=None):
    """
    Sérialise un arbre ComicInfo en bytes avec le format exact attendu :
    - Déclaration : <?xml version="1.0"?>  (sans encoding dans la déclaration)
    - Fins de ligne : \\r\\n
    - Balise <ComicInfo ...> : préservée telle quelle depuis original_bytes (xmlns inclus)
    - Éléments enfants de <ComicInfo> : 2 espaces d'indentation
    - Éléments <Page> dans <Pages> : 4 espaces d'indentation, auto-fermants
    - Ordre des attributs de <Page> : Image, ImageSize, ImageWidth, ImageHeight, puis les autres
    """
    import re as _re
    lines = ['<?xml version="1.0"?>']

    # Balise ouvrante <ComicInfo> : extraite des bytes originaux pour préserver xmlns
    comic_info_open = '<ComicInfo>'
    if original_bytes:
        m = _re.search(rb'<ComicInfo[^>]*>', original_bytes)
        if m:
            comic_info_open = m.group(0).decode('utf-8')
    lines.append(comic_info_open)

    for child in root:
        if child.tag == 'Pages':
            lines.append('  <Pages>')
            for page in child:
                # Ordre canonique des attributs
                ordered_keys = ['Image', 'ImageSize', 'ImageWidth', 'ImageHeight']
                extra_keys = [k for k in page.attrib if k not in ordered_keys]
                all_keys = [k for k in ordered_keys if k in page.attrib] + extra_keys
                attrs = ''.join(f' {k}="{page.attrib[k]}"' for k in all_keys)
                lines.append(f'    <Page{attrs} />')
            lines.append('  </Pages>')
        elif child.tag == 'MosaicViewTrace':
            # Élément à attributs (date, url), auto-fermant — comme <Page>
            ordered_keys = ['date', 'url']
            extra_keys = [k for k in child.attrib if k not in ordered_keys]
            all_keys = [k for k in ordered_keys if k in child.attrib] + extra_keys
            attrs = ''.join(f' {k}="{child.attrib[k]}"' for k in all_keys)
            lines.append(f'  <MosaicViewTrace{attrs} />')
        else:
            # Élément simple : texte éventuel
            text = child.text or ''
            # Échappe les caractères XML spéciaux dans le texte
            text = (text.replace('&', '&amp;')
                        .replace('<', '&lt;')
                        .replace('>', '&gt;')
                        .replace('"', '&quot;'))
            lines.append(f'  <{child.tag}>{text}</{child.tag}>')

    lines.append('</ComicInfo>')

    content = '\r\n'.join(lines)
    return content.encode('utf-8')


def parse_comic_info_xml(xml_data):
    try:
        root = _safe_fromstring(xml_data)
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


def read_comic_info(filepath):
    """Retourne un dictionnaire de métadonnées, ou None si aucun ComicInfo.xml
    n'est trouvé dans l'archive (CBZ/CBR/CBT)."""
    try:
        ext = os.path.splitext(filepath)[1].lower()

        if ext == '.cbz':
            with zipfile.ZipFile(filepath, 'r') as archive:
                xml_files = [f for f in archive.namelist() if f.lower().endswith('comicinfo.xml')]
                if xml_files:
                    xml_data = archive.read(xml_files[0])
                    return parse_comic_info_xml(xml_data)

        elif ext == '.cbr' and rarfile is not None:
            with rarfile.RarFile(filepath, 'r') as archive:
                xml_files = [f for f in archive.namelist() if f.lower().endswith('comicinfo.xml')]
                if xml_files:
                    xml_data = archive.read(xml_files[0])
                    return parse_comic_info_xml(xml_data)

        elif ext == '.cbt':
            with tarfile.open(filepath, 'r:*') as archive:
                xml_files = [m.name for m in archive.getmembers()
                             if m.isfile() and m.name.lower().endswith('comicinfo.xml')]
                if xml_files:
                    xml_data = archive.extractfile(archive.getmember(xml_files[0])).read()
                    return parse_comic_info_xml(xml_data)

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

    xml_entry = None
    for e in st.images_data:
        if e.get("orig_name", "").lower().endswith("comicinfo.xml"):
            xml_entry = e
            break

    if xml_entry and xml_entry.get("bytes"):
        try:
            raw = xml_entry["bytes"]
            if isinstance(raw, str):
                raw = raw.encode("utf-8")
            # Tenter UTF-8 puis latin-1 en fallback
            try:
                root = _safe_fromstring(raw)
            except ET.ParseError:
                root = _safe_fromstring(raw.decode("latin-1").encode("utf-8"))
            original_bytes = raw
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
        xml_entry["bytes"] = new_bytes
    else:
        st.images_data.append({
            "orig_name": "ComicInfo.xml", "name": "ComicInfo.xml",
            "bytes": new_bytes, "is_image": False,
            "is_dir": False, "extension": ".xml",
        })

    st.comic_metadata = parse_comic_info_xml(new_bytes)
    st.modified = True

    # Si le XML n'avait pas de <Pages>, les construire depuis images_data
    if st.comic_metadata and 'pages' not in st.comic_metadata:
        # Injecter <Pages> vide dans le XML pour que sync_pages_in_xml_data puisse le remplir
        xml_entry_now = next(
            (e for e in st.images_data if e.get('orig_name', '').lower().endswith('comicinfo.xml')),
            None
        )
        if xml_entry_now and xml_entry_now.get('bytes'):
            try:
                root_now = _safe_fromstring(xml_entry_now['bytes'])
                ET.SubElement(root_now, 'Pages')
                xml_entry_now['bytes'] = _serialize_comic_xml(root_now, xml_entry_now['bytes'])
                st.comic_metadata['pages'] = []
                sync_pages_in_xml_data(st, emit_signal=False)
            except Exception:
                pass

    metadata_signal.emit()


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
        if entry.get("is_image") and not entry.get("is_dir") and not entry.get('orig_name', '').lower().endswith('comicinfo.xml'):
            count += 1
    return count


def has_comic_info_entry(state):
    return any(entry.get('orig_name', '').lower().endswith('comicinfo.xml') for entry in state.images_data)


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
        xml_entry = None
        for entry in state.images_data:
            if entry.get('orig_name', '').lower().endswith('comicinfo.xml'):
                xml_entry = entry
                break

        if not xml_entry or not xml_entry.get('bytes'):
            return False

        _original_xml_bytes = xml_entry['bytes']
        root = _safe_fromstring(_original_xml_bytes)

        page_count_elem = root.find('PageCount')
        if page_count_elem is not None:
            page_count_elem.text = str(new_page_count)
        else:
            page_count_elem = ET.SubElement(root, 'PageCount')
            page_count_elem.text = str(new_page_count)

        xml_string = _serialize_comic_xml(root, _original_xml_bytes)
        xml_entry['bytes'] = xml_string

        state.comic_metadata['page_count'] = str(new_page_count)
        state.original_page_count = new_page_count
        state.modified = True

        return True

    except Exception:
        return False


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
        xml_entry = None
        for e in state.images_data:
            if e.get('orig_name', '').lower().endswith('comicinfo.xml'):
                xml_entry = e
                break
        if not xml_entry or not xml_entry.get('bytes'):
            return

        _original_xml_bytes = xml_entry['bytes']
        root = _safe_fromstring(_original_xml_bytes)
        pages_elem = root.find('Pages')
        if pages_elem is None:
            return

        attrs_by_id = getattr(state, '_page_attrs_by_entry_id', {})

        real_entries = [
            e for e in state.images_data
            if e.get('is_image') and not e.get('is_dir')
            and not e.get('orig_name', '').lower().endswith('comicinfo.xml')
        ]

        for page in list(pages_elem):
            pages_elem.remove(page)

        import io
        from PIL import Image as _PIL_Image
        new_pages_meta = []
        new_attrs_by_id = {}
        for new_idx, entry in enumerate(real_entries):
            attrs = dict(attrs_by_id.get(id(entry), {}))
            if not attrs and entry.get('bytes'):
                try:
                    # Utilise les dimensions déjà stockées si disponibles
                    w = entry.get('img_width')
                    h = entry.get('img_height')
                    if not w or not h:
                        img = _PIL_Image.open(io.BytesIO(entry['bytes']))
                        w, h = img.size
                        img.close()
                    attrs['ImageWidth'] = str(w)
                    attrs['ImageHeight'] = str(h)
                    attrs['ImageSize'] = str(len(entry['bytes']))
                except Exception:
                    pass
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

        xml_string = _serialize_comic_xml(root, _original_xml_bytes)
        xml_entry['bytes'] = xml_string
        state.modified = True

        if emit_signal:
            from modules.qt.metadata_signal import metadata_signal
            metadata_signal.emit()

    except Exception:
        pass


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
        if e.get('is_image') and not e.get('is_dir')
        and not e.get('orig_name', '').lower().endswith('comicinfo.xml')
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
        if e.get('is_dir') or e.get('orig_name', '').lower().endswith('comicinfo.xml'):
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
    import io
    from PIL import Image as _PIL_Image

    if not state.images_data or not state.comic_metadata:
        return
    if 'pages' not in state.comic_metadata:
        return

    try:
        xml_entry = None
        for e in state.images_data:
            if e.get('orig_name', '').lower().endswith('comicinfo.xml'):
                xml_entry = e
                break
        if not xml_entry or not xml_entry.get('bytes'):
            return

        _original_xml_bytes = xml_entry['bytes']
        root = _safe_fromstring(_original_xml_bytes)
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
                img = _PIL_Image.open(io.BytesIO(entry['bytes']))
                w, h = img.size
                img.close()
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
            xml_string = _serialize_comic_xml(root, _original_xml_bytes)
            xml_entry['bytes'] = xml_string
            state.modified = True
            if emit_signal:
                from modules.qt.metadata_signal import metadata_pages_signal
                metadata_pages_signal.emit()

    except Exception:
        pass
