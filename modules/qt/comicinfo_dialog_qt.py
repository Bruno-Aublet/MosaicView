"""
modules/qt/comicinfo_dialog_qt.py — Fenêtre de création / édition de fichier ComicInfo.xml.

Mode création : inject_fn(filename, xml_bytes) ajoute l'entrée dans la mosaïque.
Mode édition  : edit_fn(new_filename, xml_bytes) met à jour entry["bytes"] dans la mosaïque.

Points d'entrée publics :
    show_comicinfo_create_dialog(parent, inject_fn, state)
    show_comicinfo_edit_dialog(parent, entry, edit_fn, state)
"""

import json
import os
import sys
import xml.etree.ElementTree as ET

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QComboBox, QScrollArea, QWidget,
    QSizePolicy, QFrame,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIntValidator, QStandardItemModel, QStandardItem, QColor, QFont

from modules.qt.localization import _, _wt
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font


# Codes ISO 639-1 proposés dans la combobox LanguageISO (langues réelles uniquement)
_ISO_LANGUAGE_CODES = [
    "ar", "bg", "cs", "da", "de", "el", "en", "es", "et", "fi",
    "fr", "ga", "hi", "hr", "hu", "hy", "id", "is", "it", "ja",
    "ko", "lt", "lv", "ms", "mt", "nl", "no", "pl", "pt", "ro",
    "sk", "sl", "sv", "ta", "th", "tr", "uk", "vi", "zh-CN", "zh-TW",
]


def _load_language_names() -> dict:
    try:
        base = getattr(sys, "_MEIPASS", os.path.abspath("."))
        path = os.path.join(base, "locales", "language_names.json")
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


_LANGUAGE_NAMES = _load_language_names()


def _get_iso_combo_items() -> list[tuple[str, str]]:
    """Retourne [(code_iso, label_traduit), ...] trié par label, pour la langue courante."""
    from modules.qt.localization import _localization_manager
    ui_lang = getattr(_localization_manager, "current_language", "en") or "en"
    names = _LANGUAGE_NAMES.get(ui_lang) or _LANGUAGE_NAMES.get("en") or {}
    items = []
    for code in _ISO_LANGUAGE_CODES:
        label = names.get(code, code)
        items.append((code, label))
    items.sort(key=lambda x: x[1].lower())
    return items


# Tags qui n'acceptent que des entiers positifs
_INT_TAGS = frozenset({
    "Number", "Count", "Volume", "AlternateNumber", "AlternateCount",
    "StoryArcNumber", "Year",
})

# Tags qui sont des combobox avec valeurs fixes.
# Structure : tag → liste de (valeur_xml, clé_i18n_label)
# clé_i18n_label = None → afficher la valeur telle quelle (nombres)
# clé_i18n_label = ""   → entrée vide (affichée via comicinfo.combo_empty)
_COMBO_ITEMS: dict[str, list[tuple[str, str | None]]] = {
    "Month": [
        ("", ""),
        ("1",  "comicinfo.month_1"),  ("2",  "comicinfo.month_2"),
        ("3",  "comicinfo.month_3"),  ("4",  "comicinfo.month_4"),
        ("5",  "comicinfo.month_5"),  ("6",  "comicinfo.month_6"),
        ("7",  "comicinfo.month_7"),  ("8",  "comicinfo.month_8"),
        ("9",  "comicinfo.month_9"),  ("10", "comicinfo.month_10"),
        ("11", "comicinfo.month_11"), ("12", "comicinfo.month_12"),
    ],
    "Day": [("", "")] + [(str(i), None) for i in range(1, 32)],
    "AgeRating": [
        ("", ""),
        ("Adults Only 18+",  "comicinfo.age_adults_only"),
        ("Early Childhood",  "comicinfo.age_early_childhood"),
        ("Everyone",         "comicinfo.age_everyone"),
        ("Everyone 10+",     "comicinfo.age_everyone_10"),
        ("G",                "comicinfo.age_g"),
        ("Kids to Adults",   "comicinfo.age_kids_to_adults"),
        ("M",                "comicinfo.age_m"),
        ("MA15+",            "comicinfo.age_ma15"),
        ("Mature 17+",       "comicinfo.age_mature_17"),
        ("PG",               "comicinfo.age_pg"),
        ("R18+",             "comicinfo.age_r18"),
        ("Rating Pending",   "comicinfo.age_rating_pending"),
        ("Teen",             "comicinfo.age_teen"),
        ("Unknown",          "comicinfo.age_unknown"),
        ("X18+",             "comicinfo.age_x18"),
    ],
    "BlackAndWhite": [
        ("", ""),
        ("Unknown", "comicinfo.yesno_unknown"),
        ("No",      "comicinfo.yesno_no"),
        ("Yes",     "comicinfo.yesno_yes"),
    ],
    "Manga": [
        ("", ""),
        ("Unknown",            "comicinfo.yesno_unknown"),
        ("No",                 "comicinfo.yesno_no"),
        ("Yes",                "comicinfo.yesno_yes"),
        ("YesAndRightToLeft",  "comicinfo.manga_yes_right_to_left"),
    ],
    "SeriesComplete": [
        ("", ""),
        ("No",  "comicinfo.yesno_no"),
        ("Yes", "comicinfo.yesno_yes"),
    ],
    # LanguageISO : marqueur spécial — peuplé dynamiquement via _get_iso_combo_items()
    "LanguageISO": None,
}

# ── Définition de tous les champs ComicInfo, organisés par section ────────────
# Chaque section : (clé_i18n_titre, [(clé_i18n_label, tag_xml, largeur), ...])
# largeur : 1 = moitié de ligne, 2 = ligne entière

_FIELDS = [
    ("comicinfo.section_series", [
        ("comicinfo.field_series",        "Series",       2),
        ("comicinfo.field_title",         "Title",        2),
        ("comicinfo.field_number",        "Number",       1),
        ("comicinfo.field_count",         "Count",        1),
        ("comicinfo.field_volume",        "Volume",       1),
        ("comicinfo.field_alternate_series",   "AlternateSeries",   2),
        ("comicinfo.field_alternate_number",   "AlternateNumber",   1),
        ("comicinfo.field_alternate_count",    "AlternateCount",    1),
        ("comicinfo.field_series_group",  "SeriesGroup",  2),
        ("comicinfo.field_story_arc",     "StoryArc",     2),
        ("comicinfo.field_story_arc_number", "StoryArcNumber", 1),
    ]),
    ("comicinfo.section_publication", [
        ("comicinfo.field_publisher",     "Publisher",    2),
        ("comicinfo.field_imprint",       "Imprint",      2),
        ("comicinfo.field_year",          "Year",         1),
        ("comicinfo.field_month",         "Month",        1),
        ("comicinfo.field_day",           "Day",          1),
        ("comicinfo.field_format",        "Format",       1),
        ("comicinfo.field_language_iso",  "LanguageISO",  1),
        ("comicinfo.field_page_count",    "PageCount",    1),
    ]),
    ("comicinfo.section_classification", [
        ("comicinfo.field_genre",         "Genre",        2),
        ("comicinfo.field_tags",          "Tags",         2),
        ("comicinfo.field_age_rating",    "AgeRating",    1),
        ("comicinfo.field_community_rating", "CommunityRating", 1),
        ("comicinfo.field_black_and_white", "BlackAndWhite", 1),
        ("comicinfo.field_manga",         "Manga",        1),
        ("comicinfo.field_series_complete", "SeriesComplete", 1),
    ]),
    ("comicinfo.section_credits", [
        ("comicinfo.field_writer",        "Writer",       2),
        ("comicinfo.field_penciller",     "Penciller",    2),
        ("comicinfo.field_inker",         "Inker",        2),
        ("comicinfo.field_colorist",      "Colorist",     2),
        ("comicinfo.field_letterer",      "Letterer",     2),
        ("comicinfo.field_cover_artist",  "CoverArtist",  2),
        ("comicinfo.field_editor",        "Editor",       2),
        ("comicinfo.field_translator",    "Translator",   2),
    ]),
    ("comicinfo.section_content", [
        ("comicinfo.field_characters",    "Characters",   2),
        ("comicinfo.field_teams",         "Teams",        2),
        ("comicinfo.field_locations",     "Locations",    2),
    ]),
    ("comicinfo.section_misc", [
        ("comicinfo.field_web",           "Web",          2),
        ("comicinfo.field_gtin",          "GTIN",         2),
        ("comicinfo.field_scan_information", "ScanInformation", 2),
        ("comicinfo.field_notes",         "Notes",        2),
        ("comicinfo.field_summary",       "Summary",      2),
        ("comicinfo.field_review",        "Review",       2),
    ]),
]


# ── Points d'entrée publics ───────────────────────────────────────────────────

def show_comicinfo_create_dialog(parent, inject_fn, state) -> None:
    dlg = _ComicInfoDialog(parent, inject_fn=inject_fn, state=state)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()


def show_comicinfo_edit_dialog(parent, entry: dict, edit_fn, state) -> None:
    dlg = _ComicInfoDialog(parent, entry=entry, edit_fn=edit_fn, state=state)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()


# ── Helpers styles ────────────────────────────────────────────────────────────

def _btn_style(theme):
    return (
        f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
        f"border: 1px solid #aaaaaa; padding: 4px 10px; }} "
        f"QPushButton:hover {{ background: {theme['separator']}; }} "
        f"QPushButton:disabled {{ color: #888888; }}"
    )


def _input_style(theme):
    return (
        f"QLineEdit {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
        f"border: 1px solid #aaaaaa; padding: 2px 6px; }}"
    )


def _combo_style(theme):
    return (
        f"QComboBox {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
        f"border: 1px solid #aaaaaa; padding: 2px 6px; }} "
        f"QComboBox QAbstractItemView {{ background: {theme['toolbar_bg']}; color: {theme['text']}; }}"
    )


def _label_style(theme):
    return f"color: {theme['text']};"


def _section_style(theme):
    return (
        f"color: {theme['text']}; font-weight: bold; "
        f"border-bottom: 1px solid {theme['separator']}; padding-bottom: 2px;"
    )


def _scroll_style(theme):
    return (
        f"QScrollArea {{ background: {theme['bg']}; border: none; }} "
        f"QWidget#scroll_content {{ background: {theme['bg']}; }}"
    )


# ── Fenêtre principale ────────────────────────────────────────────────────────

class _ComicInfoDialog(QDialog):

    def __init__(self, parent, inject_fn=None, state=None, entry=None, edit_fn=None):
        super().__init__(parent)
        self._inject_fn     = inject_fn
        self._state         = state
        self._entry         = entry
        self._edit_fn       = edit_fn
        self._edit_mode     = entry is not None
        self._center_parent = parent

        # _fields_map : tag_xml → QLineEdit
        self._fields_map: dict[str, QLineEdit] = {}
        # _section_labels : liste de (QLabel, clé_i18n)
        self._section_labels: list[tuple[QLabel, str]] = []
        # _field_labels : liste de (QLabel, clé_i18n)
        self._field_labels: list[tuple[QLabel, str]] = []

        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.setWindowFlags(self.windowFlags() | Qt.Window)
        self.resize(660, 700)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(10)

        # Le nom est toujours ComicInfo.xml — non éditable

        # ── Scroll area contenant tous les champs ──────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(self._scroll, stretch=1)

        self._scroll_content = QWidget()
        self._scroll_content.setObjectName("scroll_content")
        self._scroll.setWidget(self._scroll_content)

        self._form_layout = QVBoxLayout(self._scroll_content)
        self._form_layout.setContentsMargins(4, 4, 4, 4)
        self._form_layout.setSpacing(6)

        # Construit les sections et champs
        self._build_fields()

        # Pré-remplissage depuis l'entrée existante (mode édition)
        if self._edit_mode:
            self._populate_from_entry()

        # PageCount : rempli automatiquement, non éditable
        self._setup_page_count()

        # ── Boutons ────────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self._btn_save = QPushButton()
        self._btn_save.setDefault(True)
        self._btn_save.clicked.connect(self._on_save)

        self._btn_cancel = QPushButton()
        self._btn_cancel.clicked.connect(self.close)

        self._btn_clear = QPushButton()
        self._btn_clear.clicked.connect(self._on_clear)

        btn_row.addStretch()
        btn_row.addWidget(self._btn_save)
        btn_row.addWidget(self._btn_cancel)
        btn_row.addStretch()
        btn_row.addWidget(self._btn_clear)

        outer.addLayout(btn_row)

        # ── Langue + thème ─────────────────────────────────────────────────────
        self._retranslate()

        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: self._retranslate()
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)

    # ── Construction des champs ────────────────────────────────────────────────

    def _make_field_widget(self, tag: str):
        """Retourne un QComboBox ou QLineEdit selon le tag."""
        if tag in _COMBO_ITEMS:
            combo = QComboBox()
            combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self._fill_combo(combo, tag)
            return combo
        edit = QLineEdit()
        if tag in _INT_TAGS:
            edit.setValidator(QIntValidator(0, 99999, edit))
        return edit

    def _fill_combo(self, combo: QComboBox, tag: str):
        """Peuple la combobox avec les labels traduits ; stocke la valeur XML dans UserRole."""
        font_normal = _get_current_font(10)
        font_italic = _get_current_font(10)
        font_italic.setItalic(True)
        theme = get_current_theme()
        gray = QColor(theme.get("disabled", "#888888"))

        current_xml = combo.currentData(Qt.UserRole) if combo.count() > 0 else ""
        model = QStandardItemModel(combo)

        if tag == "LanguageISO":
            # Entrée vide
            empty_item = QStandardItem(_("comicinfo.combo_empty"))
            empty_item.setForeground(gray)
            empty_item.setFont(font_italic)
            empty_item.setData("", Qt.UserRole)
            model.appendRow(empty_item)
            # Entrée "autre" (code ISO 639-2 "und" = undetermined)
            other_item = QStandardItem(f"{_('comicinfo.combo_other_language')} (und)")
            other_item.setForeground(gray)
            other_item.setFont(font_italic)
            other_item.setData("und", Qt.UserRole)
            model.appendRow(other_item)
            for code, label in _get_iso_combo_items():
                item = QStandardItem(f"{label} ({code})")
                item.setFont(font_normal)
                item.setData(code, Qt.UserRole)
                model.appendRow(item)
        else:
            for xml_val, label_key in _COMBO_ITEMS[tag]:
                if label_key == "":
                    label = _("comicinfo.combo_empty")
                    item = QStandardItem(label)
                    item.setForeground(gray)
                    item.setFont(font_italic)
                elif label_key is None:
                    item = QStandardItem(xml_val)
                    item.setFont(font_normal)
                else:
                    item = QStandardItem(_(label_key))
                    item.setFont(font_normal)
                item.setData(xml_val, Qt.UserRole)
                model.appendRow(item)

        combo.setModel(model)
        # Restaure la sélection après repopulation
        if current_xml:
            for i in range(combo.count()):
                if combo.itemData(i, Qt.UserRole) == current_xml:
                    combo.setCurrentIndex(i)
                    break

    def _build_fields(self):
        for section_key, fields in _FIELDS:
            # Titre de section
            lbl_section = QLabel()
            lbl_section.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self._section_labels.append((lbl_section, section_key))
            self._form_layout.addWidget(lbl_section)

            # Lignes de champs : on regroupe les champs width=1 par paires
            i = 0
            while i < len(fields):
                key_i18n, tag, width = fields[i]
                if width == 2:
                    # Ligne entière
                    row = QHBoxLayout()
                    row.setSpacing(6)
                    lbl = QLabel()
                    lbl.setFixedWidth(160)
                    self._field_labels.append((lbl, key_i18n))
                    widget = self._make_field_widget(tag)
                    row.addWidget(lbl)
                    row.addWidget(widget, stretch=1)
                    self._fields_map[tag] = widget
                    self._form_layout.addLayout(row)
                    i += 1
                else:
                    # Champ demi-ligne : on essaie de le jumeler avec le suivant
                    row = QHBoxLayout()
                    row.setSpacing(6)

                    lbl1 = QLabel()
                    lbl1.setFixedWidth(120)
                    self._field_labels.append((lbl1, key_i18n))
                    widget1 = self._make_field_widget(tag)
                    row.addWidget(lbl1)
                    row.addWidget(widget1, stretch=1)
                    self._fields_map[tag] = widget1

                    if i + 1 < len(fields) and fields[i + 1][2] == 1:
                        key_i18n2, tag2, _ = fields[i + 1]
                        lbl2 = QLabel()
                        lbl2.setFixedWidth(120)
                        self._field_labels.append((lbl2, key_i18n2))
                        widget2 = self._make_field_widget(tag2)
                        row.addWidget(lbl2)
                        row.addWidget(widget2, stretch=1)
                        self._fields_map[tag2] = widget2
                        i += 2
                    else:
                        i += 1

                    self._form_layout.addLayout(row)

            self._form_layout.addSpacing(8)

        self._form_layout.addStretch()

    # ── Pré-remplissage depuis ComicInfo.xml existant ──────────────────────────

    def _setup_page_count(self):
        from modules.qt.comic_info import get_current_image_count
        count = get_current_image_count(self._state)
        edit = self._fields_map.get("PageCount")
        if edit is not None:
            edit.setText(str(count))
            edit.setReadOnly(True)

    def _populate_from_entry(self):
        raw = self._entry.get("bytes", b"")
        if not raw:
            return
        try:
            from modules.qt.comic_info import parse_comic_info_xml
            meta = parse_comic_info_xml(raw)
            if not meta:
                return
            # Correspondance clé dict → tag XML
            _key_to_tag = {
                "title": "Title", "series": "Series", "number": "Number",
                "count": "Count", "volume": "Volume",
                "alternate_series": "AlternateSeries",
                "alternate_number": "AlternateNumber",
                "alternate_count": "AlternateCount",
                "series_group": "SeriesGroup", "story_arc": "StoryArc",
                "story_arc_number": "StoryArcNumber", "publisher": "Publisher",
                "imprint": "Imprint", "year": "Year", "month": "Month",
                "day": "Day", "format": "Format", "language_iso": "LanguageISO",
                "page_count": "PageCount", "genre": "Genre", "tags": "Tags",
                "age_rating": "AgeRating", "community_rating": "CommunityRating",
                "black_and_white": "BlackAndWhite", "manga": "Manga",
                "series_complete": "SeriesComplete", "writer": "Writer",
                "penciller": "Penciller", "inker": "Inker",
                "colorist": "Colorist", "letterer": "Letterer",
                "cover_artist": "CoverArtist", "editor": "Editor",
                "translator": "Translator", "characters": "Characters",
                "teams": "Teams", "locations": "Locations", "web": "Web",
                "gtin": "GTIN", "scan_information": "ScanInformation",
                "notes": "Notes", "summary": "Summary", "review": "Review",
            }
            for dict_key, tag in _key_to_tag.items():
                value = meta.get(dict_key, "")
                if value and tag in self._fields_map:
                    widget = self._fields_map[tag]
                    if isinstance(widget, QComboBox):
                        xml_val = str(value)
                        for i in range(widget.count()):
                            if widget.itemData(i, Qt.UserRole) == xml_val:
                                widget.setCurrentIndex(i)
                                break
                    else:
                        widget.setText(str(value))
        except Exception:
            pass

    # ── Centrage à l'affichage ─────────────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        if self._center_parent and not event.spontaneous():
            p = self._center_parent
            QTimer.singleShot(0, lambda: self._center_on(p))

    def _center_on(self, parent):
        from modules.qt.dialogs_qt import _center_on_widget
        _center_on_widget(self, parent)

    # ── Traduction + thème ─────────────────────────────────────────────────────

    def _retranslate(self):
        theme = get_current_theme()
        font = _get_current_font(10)
        font_bold = _get_current_font(10)

        title_key = "comicinfo.window_title_edit" if self._edit_mode else "comicinfo.window_title_create"
        self.setWindowTitle(_wt(title_key))
        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}"
        )

        self._scroll.setStyleSheet(_scroll_style(theme))
        self._scroll_content.setStyleSheet(f"background: {theme['bg']};")

        for lbl, section_key in self._section_labels:
            lbl.setText(_(section_key))
            lbl.setFont(font_bold)
            lbl.setStyleSheet(_section_style(theme))

        for lbl, field_key in self._field_labels:
            lbl.setText(_(field_key))
            lbl.setFont(font)
            lbl.setStyleSheet(_label_style(theme))

        input_ss   = _input_style(theme)
        combo_ss   = _combo_style(theme)
        readonly_ss = (
            f"QLineEdit {{ background: {theme['separator']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 2px 6px; }}"
        )
        for tag, widget in self._fields_map.items():
            widget.setFont(font)
            if isinstance(widget, QComboBox):
                widget.setStyleSheet(combo_ss)
                self._fill_combo(widget, tag)
            elif tag == "PageCount":
                widget.setStyleSheet(readonly_ss)
            else:
                widget.setStyleSheet(input_ss)

        btn_style = _btn_style(theme)
        save_key = "comicinfo.btn_save" if self._edit_mode else "comicinfo.btn_create"
        self._btn_save.setText(_(save_key))
        self._btn_save.setFont(font)
        self._btn_save.setStyleSheet(btn_style)

        self._btn_cancel.setText(_("buttons.cancel"))
        self._btn_cancel.setFont(font)
        self._btn_cancel.setStyleSheet(btn_style)

        self._btn_clear.setText(_("comicinfo.btn_clear"))
        self._btn_clear.setFont(font)
        self._btn_clear.setStyleSheet(btn_style)

    # ── Construction du XML à partir des champs ────────────────────────────────

    def _build_xml_bytes(self) -> bytes:
        from modules.qt.comic_info import get_current_image_count
        page_count = get_current_image_count(self._state)
        root = ET.Element("ComicInfo")
        for tag, widget in self._fields_map.items():
            if tag == "PageCount":
                child = ET.SubElement(root, tag)
                child.text = str(page_count)
                continue
            if isinstance(widget, QComboBox):
                value = (widget.currentData(Qt.UserRole) or "").strip()
            else:
                value = widget.text().strip()
            if value:
                child = ET.SubElement(root, tag)
                child.text = value

        # Préserve les Pages existantes en mode édition
        if self._edit_mode:
            raw = self._entry.get("bytes", b"")
            if raw:
                try:
                    orig_root = ET.fromstring(raw)
                    pages_elem = orig_root.find("Pages")
                    if pages_elem is not None:
                        root.append(pages_elem)
                except Exception:
                    pass

        from modules.qt.comic_info import _serialize_comic_xml
        original_bytes = self._entry.get("bytes") if self._edit_mode else None
        return _serialize_comic_xml(root, original_bytes)

    # ── Actions ────────────────────────────────────────────────────────────────

    def _on_clear(self):
        for tag, widget in self._fields_map.items():
            if tag == "PageCount":
                continue
            if isinstance(widget, QComboBox):
                widget.setCurrentIndex(0)
            else:
                widget.clear()

    def _on_save(self):
        xml_bytes = self._build_xml_bytes()
        filename = "ComicInfo.xml"
        if self._edit_mode:
            self._edit_fn(filename, xml_bytes)
        else:
            self._inject_fn(filename, xml_bytes)

        self.close()

    # ── Nettoyage ─────────────────────────────────────────────────────────────

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass
