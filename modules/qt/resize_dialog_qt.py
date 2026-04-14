"""
modules/qt/resize_dialog_qt.py — Redimensionnement d'images (version PySide6)

Reproduit rigoureusement le comportement de Modules_OLD/resize_dialog.py (tkinter).

Deux fenêtres :
  - ResizeDialog      : fenêtre principale (dimensions custom + pourcentages prédéfinis)
  - OutlierDialog     : fenêtre secondaire pour les pages aux dimensions aberrantes
"""

import io
import os
import threading
from collections import Counter

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QRadioButton, QButtonGroup, QCheckBox, QLineEdit,
    QScrollArea, QWidget, QFrame,
)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QPixmap, QImage, QIcon, QCursor, QIntValidator

from modules.qt.localization import _, _wt
from modules.qt.state import get_current_theme
from modules.qt import state as _state_module
from modules.qt.font_loader import resource_path
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.canvas_overlay_qt import show_canvas_text as _show_canvas_text, hide_canvas_text as _hide_canvas_text
from modules.qt.entries import (
    detect_jpeg_quality,
    free_image_memory,
)
from modules.qt.mosaic_canvas import build_qimage_for_entry
from modules.qt.dialogs_qt import MsgDialog


# ─────────────────────────────────────────────────────────────────────────────
# Helpers langue
# ─────────────────────────────────────────────────────────────────────────────

def _connect_lang(dialog, handler):
    from modules.qt.language_signal import language_signal
    dialog._lang_handler = handler
    language_signal.changed.connect(dialog._lang_handler)
    dialog.finished.connect(lambda: _disconnect_lang(dialog))


def _disconnect_lang(dialog):
    from modules.qt.language_signal import language_signal
    try:
        language_signal.changed.disconnect(dialog._lang_handler)
    except RuntimeError:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Texte de progression sur le canvas (même pattern que conversion_dialogs_qt.py)
# ─────────────────────────────────────────────────────────────────────────────

def _show_resizing_text(canvas, percent: int, item_holder: list):
    _show_canvas_text(canvas, _("labels.resizing", percent=percent), item_holder)


def _hide_resizing_text(canvas, item_holder: list):
    _hide_canvas_text(canvas, item_holder)


# ─────────────────────────────────────────────────────────────────────────────
# Algorithme de clustering (logique pure, port direct depuis resize_dialog.py)
# ─────────────────────────────────────────────────────────────────────────────

def cluster_and_find_reference(dimensions, tolerance=0.10):
    """
    Regroupe les dimensions similaires (±tolérance%) et retourne :
    - La dimension de référence (moyenne du cluster principal)
    - Un mapping dimension→multiplicateur entier
    - La liste des outliers (dimensions aberrantes)
    """
    if not dimensions:
        return 0, {}, set()

    dim_counts = Counter(dimensions)
    sorted_dims = sorted(set(dimensions))
    clusters = []
    for dim in sorted_dims:
        added = False
        for cluster in clusters:
            cluster_avg = sum(cluster) / len(cluster)
            if abs(dim - cluster_avg) / cluster_avg <= tolerance:
                cluster.append(dim)
                added = True
                break
        if not added:
            clusters.append([dim])

    def cluster_total_count(cluster):
        return sum(dim_counts[d] for d in cluster)

    main_cluster = max(clusters, key=cluster_total_count)
    reference = sum(main_cluster) / len(main_cluster)

    outliers = set()
    for cluster in clusters:
        cluster_avg = sum(cluster) / len(cluster)
        ratio = cluster_avg / reference
        if cluster is not main_cluster and (ratio < 0.75 or ratio > 2.5):
            outliers.update(cluster)

    dim_to_multiplier = {}
    for cluster in clusters:
        cluster_avg = sum(cluster) / len(cluster)
        raw_ratio = cluster_avg / reference
        multiplier = round(raw_ratio)
        for dim in cluster:
            if dim not in outliers:
                dim_to_multiplier[dim] = multiplier

    return reference, dim_to_multiplier, outliers


# ─────────────────────────────────────────────────────────────────────────────
# Dialogue outliers
# ─────────────────────────────────────────────────────────────────────────────

class OutlierDialog(QDialog):
    """Dialogue secondaire pour traiter les pages aux dimensions aberrantes."""

    def __init__(self, parent, outlier_pages, target_width, target_height,
                 reference_width, reference_height, use_custom_dimensions):
        super().__init__(parent)
        self.setModal(True)
        self._result = None          # None = annulé, dict = choix utilisateur

        ico_path = resource_path("icons/MosaicView.ico")
        if os.path.exists(ico_path):
            self.setWindowIcon(QIcon(ico_path))

        self._outlier_pages       = outlier_pages
        self._target_width        = target_width
        self._target_height       = target_height
        self._reference_width     = reference_width
        self._reference_height    = reference_height
        self._use_custom_dim      = use_custom_dimensions

        h = min(500, 150 + len(outlier_pages) * 120)
        self.resize(600, h)

        # ── Layout principal ──────────────────────────────────────────────────
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 10, 12, 10)
        main_layout.setSpacing(6)

        self._msg_lbl = QLabel()
        self._msg_lbl.setWordWrap(True)
        self._msg_lbl.setAlignment(Qt.AlignLeft)
        main_layout.addWidget(self._msg_lbl)

        # Zone scrollable
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self._scroll_content = QWidget()
        self._scroll_layout  = QVBoxLayout(self._scroll_content)
        self._scroll_layout.setContentsMargins(0, 0, 0, 0)
        self._scroll_layout.setSpacing(6)
        scroll.setWidget(self._scroll_content)
        main_layout.addWidget(scroll, stretch=1)

        # Boutons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._btn_continue = QPushButton()
        self._btn_continue.setFixedWidth(120)
        self._btn_continue.setEnabled(False)
        self._btn_continue.setDefault(True)
        self._btn_continue.clicked.connect(self._on_continue)
        btn_row.addWidget(self._btn_continue)
        self._btn_cancel = QPushButton()
        self._btn_cancel.setFixedWidth(120)
        self._btn_cancel.clicked.connect(self._on_cancel)
        btn_row.addWidget(self._btn_cancel)
        btn_row.addStretch()
        main_layout.addLayout(btn_row)

        # Construit les widgets pour chaque page outlier
        # choice_vars[page_name] = {'width': QButtonGroup, 'height': QButtonGroup}
        self._choice_groups: dict[str, dict] = {}
        self._build_page_widgets()

        self._retranslate()
        _connect_lang(self, lambda _: self._retranslate())
        self._center_parent = parent

    def showEvent(self, event):
        super().showEvent(event)
        if self._center_parent and not event.spontaneous():
            from PySide6.QtCore import QTimer
            from modules.qt.dialogs_qt import _center_on_widget
            p = self._center_parent
            QTimer.singleShot(0, lambda: _center_on_widget(self, p))

    # ── Construction des widgets pages ────────────────────────────────────────

    def _build_page_widgets(self):
        from PIL import Image

        for page_info in self._outlier_pages:
            entry               = page_info["entry"]
            has_width_outlier   = page_info["has_width_outlier"]
            has_height_outlier  = page_info["has_height_outlier"]

            try:
                temp_img = Image.open(io.BytesIO(entry["bytes"]))
                img_w = temp_img.width
                img_h = temp_img.height
                temp_img.close()
            except Exception:
                continue

            # Cadre par page
            frame = QFrame()
            frame.setFrameShape(QFrame.StyledPanel)
            frame.setLineWidth(1)
            frame_layout = QHBoxLayout(frame)
            frame_layout.setContentsMargins(8, 6, 8, 6)
            frame_layout.setSpacing(8)

            # Colonne gauche : texte + radio buttons
            left_w = QWidget()
            left_layout = QVBoxLayout(left_w)
            left_layout.setContentsMargins(0, 0, 0, 0)
            left_layout.setSpacing(2)

            name_lbl = QLabel(f"• {entry['orig_name']}")
            name_font = _get_current_font(10)
            name_font.setBold(True)
            name_lbl.setFont(name_font)
            name_lbl.setWordWrap(True)
            left_layout.addWidget(name_lbl)

            page_name = entry["orig_name"]
            self._choice_groups[page_name] = {}

            # Largeur outlier
            if (has_width_outlier or has_height_outlier) and self._use_custom_dim and self._target_width:
                info_lbl = QLabel(
                    f"    {_('dialogs.outliers.width')}: {img_w}px ({_('dialogs.outliers.unusual')})"
                )
                info_lbl.setFont(_get_current_font(9))
                left_layout.addWidget(info_lbl)

                grp = QButtonGroup(self)
                self._choice_groups[page_name]["width"] = grp

                ratio = img_w / self._reference_width if self._reference_width else 1
                possible_mults = [1, 2]
                if ratio > 2.5: possible_mults.append(4)
                if ratio > 5:   possible_mults.append(8)

                for mult in possible_mults:
                    final_size = self._target_width * mult
                    rb = QRadioButton(f"×{mult} ({img_w} → {final_size}px)")
                    rb.setFont(_get_current_font(9))
                    rb.setProperty("mult_value", mult)
                    rb.toggled.connect(self._check_all_selected)
                    grp.addButton(rb)
                    left_layout.addWidget(rb)

                rb_skip = QRadioButton(
                    f"{_('dialogs.outliers.skip')} ({_('dialogs.outliers.keep')} {img_w}px)"
                )
                rb_skip.setFont(_get_current_font(9))
                rb_skip.setProperty("mult_value", 0)
                rb_skip.toggled.connect(self._check_all_selected)
                grp.addButton(rb_skip)
                left_layout.addWidget(rb_skip)

            # Hauteur outlier
            if (has_width_outlier or has_height_outlier) and self._use_custom_dim and self._target_height:
                info_lbl = QLabel(
                    f"    {_('dialogs.outliers.height')}: {img_h}px ({_('dialogs.outliers.unusual')})"
                )
                info_lbl.setFont(_get_current_font(9))
                left_layout.addWidget(info_lbl)

                grp = QButtonGroup(self)
                self._choice_groups[page_name]["height"] = grp

                ratio = img_h / self._reference_height if self._reference_height else 1
                possible_mults = [1, 2]
                if ratio > 2.5: possible_mults.append(4)
                if ratio > 5:   possible_mults.append(8)

                for mult in possible_mults:
                    final_size = self._target_height * mult
                    rb = QRadioButton(f"×{mult} ({img_h} → {final_size}px)")
                    rb.setFont(_get_current_font(9))
                    rb.setProperty("mult_value", mult)
                    rb.toggled.connect(self._check_all_selected)
                    grp.addButton(rb)
                    left_layout.addWidget(rb)

                rb_skip = QRadioButton(
                    f"{_('dialogs.outliers.skip')} ({_('dialogs.outliers.keep')} {img_h}px)"
                )
                rb_skip.setFont(_get_current_font(9))
                rb_skip.setProperty("mult_value", 0)
                rb_skip.toggled.connect(self._check_all_selected)
                grp.addButton(rb_skip)
                left_layout.addWidget(rb_skip)

            left_layout.addStretch()
            frame_layout.addWidget(left_w, stretch=1)

            # Vignette droite
            try:
                from PIL import Image as _PILImage
                if entry.get("large_thumb_pil") is not None:
                    thumb = entry["large_thumb_pil"].copy()
                else:
                    thumb = _PILImage.open(io.BytesIO(entry["bytes"]))
                thumb.thumbnail((150, 150), _PILImage.Resampling.LANCZOS)
                buf = io.BytesIO()
                thumb.save(buf, format="PNG")
                buf.seek(0)
                qpix = QPixmap.fromImage(QImage.fromData(buf.read()))
                thumb_lbl = QLabel()
                thumb_lbl.setPixmap(qpix)
                thumb_lbl.setAlignment(Qt.AlignCenter)
                thumb_lbl.setFixedWidth(160)
                frame_layout.addWidget(thumb_lbl, stretch=0)
            except Exception:
                pass

            self._scroll_layout.addWidget(frame)

        self._scroll_layout.addStretch()

    # ── Vérification que tout est sélectionné ─────────────────────────────────

    def _check_all_selected(self):
        all_ok = True
        for page_name, dims in self._choice_groups.items():
            for dim_type, grp in dims.items():
                if grp.checkedButton() is None:
                    all_ok = False
                    break
            if not all_ok:
                break
        self._btn_continue.setEnabled(all_ok)

    # ── Retranslate ───────────────────────────────────────────────────────────

    def _retranslate(self):
        theme = get_current_theme()
        self.setWindowTitle(_wt("dialogs.outliers.title"))
        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}"
            f"QFrame  {{ background: {theme['toolbar_bg']}; color: {theme['text']}; }}"
        )
        self._msg_lbl.setText(_("dialogs.outliers.message"))
        self._msg_lbl.setFont(_get_current_font(12))
        self._msg_lbl.setStyleSheet(f"color: {theme['text']};")

        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        self._btn_continue.setText(_("buttons.continue"))
        self._btn_continue.setFont(_get_current_font(10))
        self._btn_continue.setStyleSheet(btn_style)
        self._btn_cancel.setText(_("buttons.cancel"))
        self._btn_cancel.setFont(_get_current_font(10))
        self._btn_cancel.setStyleSheet(btn_style)

    # ── Actions ───────────────────────────────────────────────────────────────

    def _on_continue(self):
        choices = {}
        for page_name, dims in self._choice_groups.items():
            choices[page_name] = {}
            w_grp = dims.get("width")
            h_grp = dims.get("height")
            if w_grp and w_grp.checkedButton():
                choices[page_name]["width_mult"] = w_grp.checkedButton().property("mult_value")
            else:
                choices[page_name]["width_mult"] = None
            if h_grp and h_grp.checkedButton():
                choices[page_name]["height_mult"] = h_grp.checkedButton().property("mult_value")
            else:
                choices[page_name]["height_mult"] = None
        self._result = choices
        self.accept()

    def _on_cancel(self):
        self._result = None
        self.reject()


# ─────────────────────────────────────────────────────────────────────────────
# Dialogue principal de redimensionnement
# ─────────────────────────────────────────────────────────────────────────────

# État global mémorisé entre ouvertures (identique à l'original tkinter)
_multi_page_checkbox_state = True


class ResizeDialog(QDialog):
    """
    Fenêtre principale de redimensionnement d'images.
    Reproduit rigoureusement reduce_selected_images_size() de resize_dialog.py.
    """

    def __init__(self, parent, selected_entries, callbacks):
        super().__init__(parent)
        global _multi_page_checkbox_state

        from modules.qt.overlay_tooltip_qt import OverlayTooltip
        self._overlay_tip = OverlayTooltip(self)

        self._selected_entries = selected_entries
        self._callbacks        = callbacks
        self._updating         = False
        self._multi_page_state = _multi_page_checkbox_state

        self.setModal(True)
        self.resize(700, 560)

        ico_path = resource_path("icons/MosaicView.ico")
        if os.path.exists(ico_path):
            self.setWindowIcon(QIcon(ico_path))

        # ── Pré-calculs ───────────────────────────────────────────────────────
        from PIL import Image
        self._total_size_mb = 0.0
        dimensions_set = set()

        for entry in selected_entries:
            if entry.get("bytes"):
                self._total_size_mb += len(entry["bytes"]) / (1024 * 1024)
                try:
                    img = Image.open(io.BytesIO(entry["bytes"]))
                    dimensions_set.add((img.width, img.height))
                except Exception:
                    pass

        self._same_dim = len(dimensions_set) == 1
        if self._same_dim:
            self._cur_w, self._cur_h = list(dimensions_set)[0]
            self._aspect = self._cur_w / self._cur_h
        else:
            self._cur_w = self._cur_h = 0
            self._aspect = 1.0

        self._nb_files = len(selected_entries)

        # ── Construction de l'UI ──────────────────────────────────────────────
        self._build_ui()
        self._retranslate()
        _connect_lang(self, lambda _: self._retranslate())
        if self._multi_page_cb is not None:
            import html as _html
            def _tip_html():
                text = _("dialogs.reduce_size.multi_page_width_tooltip")
                escaped = _html.escape(text).replace("\n", "<br>")
                return (
                    f'<table style="max-width:360px;white-space:normal;">'
                    f'<tr><td>{escaped}</td></tr>'
                    f'</table>'
                )
            self._overlay_tip.track(self._multi_page_cb, _tip_html())

        self._center_parent = parent

    def showEvent(self, event):
        super().showEvent(event)
        if self._center_parent and not event.spontaneous():
            from PySide6.QtCore import QTimer
            from modules.qt.dialogs_qt import _center_on_widget
            p = self._center_parent
            QTimer.singleShot(0, lambda: _center_on_widget(self, p))

    # ── Construction UI ───────────────────────────────────────────────────────

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 15, 20, 15)
        main_layout.setSpacing(8)

        # Titre
        self._title_lbl = QLabel()
        self._title_lbl.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(self._title_lbl)

        # Infos actuelles
        self._info_lbl = QLabel()
        self._info_lbl.setAlignment(Qt.AlignCenter)
        self._info_lbl.setWordWrap(True)
        main_layout.addWidget(self._info_lbl)

        # ── Section dimensions personnalisées ─────────────────────────────────
        self._custom_title_lbl = QLabel()
        self._custom_title_lbl.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(self._custom_title_lbl)

        self._page_width_info_lbl = QLabel()
        self._page_width_info_lbl.setAlignment(Qt.AlignCenter)
        self._page_width_info_lbl.setWordWrap(True)
        main_layout.addWidget(self._page_width_info_lbl)

        # Warning dimensions différentes (caché par défaut, ne prend pas d'espace)
        self._diff_dim_lbl = QLabel()
        self._diff_dim_lbl.setAlignment(Qt.AlignCenter)
        self._diff_dim_lbl.setWordWrap(True)
        self._diff_dim_lbl.setStyleSheet("color: red;")
        self._diff_dim_lbl.setVisible(False)
        from PySide6.QtWidgets import QSizePolicy
        sp = self._diff_dim_lbl.sizePolicy()
        sp.setRetainSizeWhenHidden(False)
        self._diff_dim_lbl.setSizePolicy(sp)
        main_layout.addWidget(self._diff_dim_lbl)

        main_layout.addSpacing(16)

        # Ligne largeur / hauteur — deux groupes collés, centrés via stretch
        dim_row = QHBoxLayout()
        dim_row.setSpacing(0)
        dim_row.setContentsMargins(0, 0, 0, 0)
        dim_row.addStretch()

        # Groupe Largeur
        self._width_lbl = QLabel()
        self._width_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._width_lbl.setFixedWidth(80)
        dim_row.addWidget(self._width_lbl)
        dim_row.addSpacing(4)

        self._width_edit = QLineEdit()
        self._width_edit.setFixedWidth(80)
        self._width_edit.setValidator(QIntValidator(1, 99999, self))
        self._width_edit.setText(str(self._cur_w) if self._same_dim else "")
        dim_row.addWidget(self._width_edit)
        dim_row.addSpacing(3)

        self._width_px_lbl = QLabel("px")
        dim_row.addWidget(self._width_px_lbl)

        # Séparateur minimal entre les deux groupes
        dim_row.addSpacing(12)

        # Groupe Hauteur
        self._height_lbl = QLabel()
        self._height_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._height_lbl.setFixedWidth(80)
        dim_row.addWidget(self._height_lbl)
        dim_row.addSpacing(4)

        self._height_edit = QLineEdit()
        self._height_edit.setFixedWidth(80)
        self._height_edit.setValidator(QIntValidator(1, 99999, self))
        self._height_edit.setText(str(self._cur_h) if self._same_dim else "")
        dim_row.addWidget(self._height_edit)
        dim_row.addSpacing(3)

        self._height_px_lbl = QLabel("px")
        dim_row.addWidget(self._height_px_lbl)

        dim_row.addStretch()
        main_layout.addLayout(dim_row)

        # Label poids estimé (custom)
        self._custom_estimated_lbl = QLabel("")
        self._custom_estimated_lbl.setAlignment(Qt.AlignCenter)
        if self._same_dim:
            main_layout.addWidget(self._custom_estimated_lbl)

        # Checkbox multi-page
        self._multi_page_cb = None
        if self._nb_files > 1:
            self._multi_page_cb = QCheckBox()
            self._multi_page_cb.setChecked(self._multi_page_state)
            self._multi_page_cb.stateChanged.connect(self._on_multi_page_toggled)
            main_layout.addWidget(self._multi_page_cb, alignment=Qt.AlignCenter)

        # Visibilité du warning dimensions différentes
        if not self._same_dim and self._nb_files > 1:
            self._diff_dim_lbl.setVisible(not self._multi_page_state)

        # Bindings édition
        self._width_edit.textChanged.connect(self._on_width_changed)
        self._height_edit.textChanged.connect(self._on_height_changed)
        self._width_edit.textChanged.connect(lambda _: self._update_ok_state())
        self._height_edit.textChanged.connect(lambda _: self._update_ok_state())

        # Estimation initiale
        if self._same_dim:
            self._update_custom_estimate()

        main_layout.addSpacing(20)

        # ── Section pourcentages ──────────────────────────────────────────────
        titles_row = QHBoxLayout()

        self._reduc_title_lbl = QLabel()
        self._reduc_title_lbl.setAlignment(Qt.AlignCenter)
        titles_row.addWidget(self._reduc_title_lbl, stretch=1)

        self._enlarg_title_lbl = QLabel()
        self._enlarg_title_lbl.setAlignment(Qt.AlignCenter)
        titles_row.addWidget(self._enlarg_title_lbl, stretch=1)

        main_layout.addLayout(titles_row)

        # Option 0% centrée
        self._rb_zero = QRadioButton()
        self._rb_zero.setChecked(True)
        main_layout.addWidget(self._rb_zero, alignment=Qt.AlignCenter)

        # Deux colonnes
        self._pct_group = QButtonGroup(self)
        self._pct_group.addButton(self._rb_zero)
        self._rb_zero.setProperty("pct_type", "zero")
        self._rb_zero.setProperty("pct_value", 0)
        self._rb_zero.toggled.connect(lambda checked: self._on_pct_selected(self._rb_zero) if checked else None)
        self._rb_zero.toggled.connect(lambda _: self._update_ok_state())

        cols_row = QHBoxLayout()
        cols_row.setSpacing(20)

        left_col_w  = QWidget()
        right_col_w = QWidget()
        left_col_layout  = QVBoxLayout(left_col_w)
        right_col_layout = QVBoxLayout(right_col_w)
        left_col_layout.setContentsMargins(10, 0, 10, 0)
        right_col_layout.setContentsMargins(10, 0, 10, 0)
        left_col_layout.setSpacing(10)
        right_col_layout.setSpacing(10)

        self._reduc_rbs  = []
        self._enlarg_rbs = []

        reduction_options = [
            ("reduce_10", 10), ("reduce_20", 20), ("reduce_25", 25),
            ("reduce_33", 33), ("reduce_50", 50), ("reduce_75", 75),
            ("reduce_90", 90),
        ]
        enlargement_options = [
            ("enlarge_10", 10), ("enlarge_20", 20), ("enlarge_25", 25),
            ("enlarge_33", 33), ("enlarge_50", 50), ("enlarge_75", 75),
            ("enlarge_100", 100),
        ]

        for key, pct in reduction_options:
            factor = (100 - pct) / 100
            est = self._total_size_mb * (factor ** 2)
            rb = QRadioButton()
            rb.setProperty("pct_type", "reduce")
            rb.setProperty("pct_value", pct)
            rb.setProperty("pct_key", key)
            rb.setProperty("pct_est", est)
            rb.toggled.connect(lambda checked, r=rb: self._on_pct_selected(r) if checked else None)
            rb.toggled.connect(lambda _: self._update_ok_state())
            self._pct_group.addButton(rb)
            self._reduc_rbs.append(rb)
            left_col_layout.addWidget(rb)

        for key, pct in enlargement_options:
            factor = (100 + pct) / 100
            est = self._total_size_mb * (factor ** 2)
            rb = QRadioButton()
            rb.setProperty("pct_type", "enlarge")
            rb.setProperty("pct_value", pct)
            rb.setProperty("pct_key", key)
            rb.setProperty("pct_est", est)
            rb.toggled.connect(lambda checked, r=rb: self._on_pct_selected(r) if checked else None)
            rb.toggled.connect(lambda _: self._update_ok_state())
            self._pct_group.addButton(rb)
            self._enlarg_rbs.append(rb)
            right_col_layout.addWidget(rb)

        left_col_layout.addStretch()
        right_col_layout.addStretch()
        cols_row.addWidget(left_col_w,  stretch=1)
        cols_row.addWidget(right_col_w, stretch=1)
        main_layout.addLayout(cols_row, stretch=1)

        # ── Boutons OK / Annuler ──────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._ok_btn = QPushButton()
        self._ok_btn.setFixedWidth(120)
        self._ok_btn.setDefault(True)
        self._ok_btn.setEnabled(False)
        self._ok_btn.clicked.connect(self._on_ok)
        btn_row.addWidget(self._ok_btn)
        btn_row.addSpacing(10)
        self._cancel_btn = QPushButton()
        self._cancel_btn.setFixedWidth(120)
        self._cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._cancel_btn)
        btn_row.addStretch()
        main_layout.addLayout(btn_row)

    # ── Activation du bouton OK ───────────────────────────────────────────────

    def _update_ok_state(self):
        """OK actif seulement si un % non-nul est coché OU une dimension différente de l'originale est saisie."""
        # Un % significatif coché ?
        checked = self._pct_group.checkedButton()
        if checked is not None and checked.property("pct_type") != "zero":
            self._ok_btn.setEnabled(True)
            return
        # Une dimension valide et différente de l'originale saisie ?
        w_str = self._width_edit.text().strip()
        h_str = self._height_edit.text().strip()
        try:
            w = int(w_str) if w_str else None
            h = int(h_str) if h_str else None
            # Au moins une dimension saisie, valide, et différente de l'originale
            if w is not None and w > 0 and w != self._cur_w:
                self._ok_btn.setEnabled(True)
                return
            if h is not None and h > 0 and h != self._cur_h:
                self._ok_btn.setEnabled(True)
                return
            # Cas dimensions inconnues (images différentes) : toute saisie valide suffit
            if not self._same_dim:
                if (w is not None and w > 0) or (h is not None and h > 0):
                    self._ok_btn.setEnabled(True)
                    return
        except ValueError:
            pass
        self._ok_btn.setEnabled(False)

    # ── Logique édition dimensions ────────────────────────────────────────────

    def _on_width_changed(self, text):
        if self._updating:
            return
        # Toute saisie dans les champs → décocher les radios
        if text.strip():
            self._pct_group.setExclusive(False)
            for btn in self._pct_group.buttons():
                btn.setChecked(False)
            self._pct_group.setExclusive(True)
        if not self._same_dim:
            return
        try:
            self._updating = True
            new_w = int(text)
            new_h = int(new_w / self._aspect)
            self._height_edit.setText(str(new_h))
            self._update_custom_estimate_from_width(new_w)
        except ValueError:
            pass
        finally:
            self._updating = False

    def _on_height_changed(self, text):
        if self._updating:
            return
        # Toute saisie dans les champs → décocher les radios
        if text.strip():
            self._pct_group.setExclusive(False)
            for btn in self._pct_group.buttons():
                btn.setChecked(False)
            self._pct_group.setExclusive(True)
        if not self._same_dim:
            return
        try:
            self._updating = True
            new_h = int(text)
            new_w = int(new_h * self._aspect)
            self._width_edit.setText(str(new_w))
            self._update_custom_estimate_from_height(new_h)
        except ValueError:
            pass
        finally:
            self._updating = False

    def _update_custom_estimate(self):
        try:
            w = int(self._width_edit.text())
            self._update_custom_estimate_from_width(w)
        except ValueError:
            pass

    def _update_custom_estimate_from_width(self, new_w):
        if self._cur_w <= 0:
            return
        scale = new_w / self._cur_w
        est = self._total_size_mb * (scale ** 2)
        self._custom_estimated_lbl.setText(
            _("dialogs.reduce_size.estimated_size", size=f"{est:.2f}")
        )

    def _update_custom_estimate_from_height(self, new_h):
        if self._cur_h <= 0:
            return
        scale = new_h / self._cur_h
        est = self._total_size_mb * (scale ** 2)
        self._custom_estimated_lbl.setText(
            _("dialogs.reduce_size.estimated_size", size=f"{est:.2f}")
        )

    def _on_pct_selected(self, rb):
        pct_type  = rb.property("pct_type")
        pct_value = rb.property("pct_value")
        if pct_type == "zero":
            if self._same_dim:
                self._updating = True
                self._width_edit.setText(str(self._cur_w))
                self._height_edit.setText(str(self._cur_h))
                self._custom_estimated_lbl.setText(
                    _("dialogs.reduce_size.estimated_size", size=f"{self._total_size_mb:.2f}")
                )
                self._updating = False
            else:
                self._updating = True
                self._width_edit.clear()
                self._height_edit.clear()
                self._updating = False
        elif pct_type == "reduce":
            # Vider les champs customs
            self._updating = True
            self._width_edit.clear()
            self._height_edit.clear()
            self._updating = False
            if self._same_dim:
                scale = (100 - pct_value) / 100
                est = self._total_size_mb * (scale ** 2)
                self._custom_estimated_lbl.setText(
                    _("dialogs.reduce_size.estimated_size", size=f"{est:.2f}")
                )
        elif pct_type == "enlarge":
            # Vider les champs customs
            self._updating = True
            self._width_edit.clear()
            self._height_edit.clear()
            self._updating = False
            if self._same_dim:
                scale = (100 + pct_value) / 100
                est = self._total_size_mb * (scale ** 2)
                self._custom_estimated_lbl.setText(
                    _("dialogs.reduce_size.estimated_size", size=f"{est:.2f}")
                )

    def _on_multi_page_toggled(self, state):
        global _multi_page_checkbox_state
        _multi_page_checkbox_state = bool(state)
        self._multi_page_state = bool(state)
        # Affiche/cache le warning dimensions différentes
        if not self._same_dim and self._nb_files > 1:
            self._diff_dim_lbl.setVisible(not bool(state))

    # ── Retranslate ───────────────────────────────────────────────────────────

    def _retranslate(self):
        theme = get_current_theme()
        self.setWindowTitle(_wt("dialogs.reduce_size.window_title"))
        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}"
            f"QRadioButton {{ background: {theme['bg']}; color: {theme['text']}; }}"
        )
        if self._multi_page_cb is not None:
            self._multi_page_cb.setStyleSheet(
                f"QCheckBox {{ background: {theme['bg']}; color: {theme['text']}; }}"
            )

        font_title  = _get_current_font(14)
        font_title.setBold(True)
        font_normal = _get_current_font(11)
        font_small  = _get_current_font(10)
        font_tiny   = _get_current_font(8)
        font_tiny_i = _get_current_font(8)
        font_tiny_i.setItalic(True)
        warn_color  = "#666666" if not (self._callbacks.get('state') or _state_module.state).dark_mode else "#999999"

        entry_style = (
            f"QLineEdit {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 2px; }}"
        )
        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )

        # Titre
        file_word = (
            _("dialogs.reduce_size.word_image") if self._nb_files == 1
            else _("dialogs.reduce_size.word_images")
        )
        self._title_lbl.setText(
            _("dialogs.reduce_size.title", count=self._nb_files, word=file_word)
        )
        self._title_lbl.setFont(font_title)
        self._title_lbl.setStyleSheet(f"color: {theme['text']};")

        # Infos
        size_text = _("dialogs.reduce_size.current_size", size=f"{self._total_size_mb:.2f}")
        if self._same_dim:
            dim_text  = _("dialogs.reduce_size.current_dimensions",
                          width=self._cur_w, height=self._cur_h)
            self._info_lbl.setText(f"{size_text}\n{dim_text}")
        else:
            self._info_lbl.setText(size_text)
        self._info_lbl.setFont(font_small)
        self._info_lbl.setStyleSheet(f"color: {theme['text']};")

        # Section custom
        custom_title_font = _get_current_font(11)
        custom_title_font.setBold(True)
        self._custom_title_lbl.setText(_("dialogs.reduce_size.custom_dimensions"))
        self._custom_title_lbl.setFont(custom_title_font)
        self._custom_title_lbl.setStyleSheet(f"color: {theme['text']};")

        self._page_width_info_lbl.setText(_("dialogs.reduce_size.page_width_info"))
        self._page_width_info_lbl.setFont(font_tiny_i)
        self._page_width_info_lbl.setStyleSheet(f"color: {warn_color};")

        if not self._same_dim and self._nb_files > 1:
            self._diff_dim_lbl.setText(_("dialogs.reduce_size.different_dimensions_warning"))
            self._diff_dim_lbl.setFont(font_small)

        self._width_lbl.setText(_("dialogs.reduce_size.width_label"))
        self._width_lbl.setFont(font_small)
        self._width_lbl.setStyleSheet(f"color: {theme['text']};")
        self._width_edit.setFont(font_small)
        self._width_edit.setStyleSheet(entry_style)
        self._width_px_lbl.setFont(font_small)
        self._width_px_lbl.setStyleSheet(f"color: {theme['text']};")

        self._height_lbl.setText(_("dialogs.reduce_size.height_label"))
        self._height_lbl.setFont(font_small)
        self._height_lbl.setStyleSheet(f"color: {theme['text']};")
        self._height_edit.setFont(font_small)
        self._height_edit.setStyleSheet(entry_style)
        self._height_px_lbl.setFont(font_small)
        self._height_px_lbl.setStyleSheet(f"color: {theme['text']};")

        self._custom_estimated_lbl.setFont(font_small)
        self._custom_estimated_lbl.setStyleSheet(f"color: {warn_color};")

        if self._multi_page_cb is not None:
            self._multi_page_cb.setText(_("dialogs.reduce_size.multi_page_width"))
            self._multi_page_cb.setFont(font_small)

        # Titres colonnes pourcentages
        pct_title_font = _get_current_font(11)
        pct_title_font.setBold(True)
        self._reduc_title_lbl.setText(_("dialogs.reduce_size.percentage_reduction"))
        self._reduc_title_lbl.setFont(pct_title_font)
        self._reduc_title_lbl.setStyleSheet(f"color: {theme['text']};")
        self._enlarg_title_lbl.setText(_("dialogs.reduce_size.percentage_enlargement"))
        self._enlarg_title_lbl.setFont(pct_title_font)
        self._enlarg_title_lbl.setStyleSheet(f"color: {theme['text']};")

        # Radio 0%
        self._rb_zero.setText(_("dialogs.reduce_size.reduce_0"))
        self._rb_zero.setFont(font_small)

        # Radios réduction
        for rb in self._reduc_rbs:
            key = rb.property("pct_key")
            est = rb.property("pct_est")
            est_text = _("dialogs.reduce_size.estimated_size", size=f"{est:.2f}")
            rb.setText(f"{_('dialogs.reduce_size.' + key)} ({est_text})")
            rb.setFont(font_small)

        # Radios agrandissement
        for rb in self._enlarg_rbs:
            key = rb.property("pct_key")
            est = rb.property("pct_est")
            est_text = _("dialogs.reduce_size.estimated_size", size=f"{est:.2f}")
            rb.setText(f"{_('dialogs.reduce_size.' + key)} ({est_text})")
            rb.setFont(font_small)

        # Boutons
        self._ok_btn.setText(_("buttons.ok"))
        self._ok_btn.setFont(font_small)
        self._ok_btn.setStyleSheet(btn_style)
        self._cancel_btn.setText(_("buttons.cancel"))
        self._cancel_btn.setFont(font_small)
        self._cancel_btn.setStyleSheet(btn_style)

    # ── Action OK ─────────────────────────────────────────────────────────────

    def _on_ok(self):
        from PIL import Image

        callbacks          = self._callbacks
        selected_entries   = self._selected_entries
        state              = callbacks.get('state') or _state_module.state
        save_state_fn      = callbacks.get("save_state",         lambda: None)
        render_mosaic_fn   = callbacks.get("render_mosaic",      lambda: None)
        update_button_text = callbacks.get("update_button_text", lambda: None)
        refresh_status_fn  = callbacks.get("refresh_status",     lambda: None)
        canvas             = callbacks.get("canvas")
        item_holder        = [None]

        # ── Détermination du mode ─────────────────────────────────────────────
        use_custom_dim = False
        target_width   = None
        target_height  = None

        width_str  = self._width_edit.text().strip()
        height_str = self._height_edit.text().strip()

        if width_str or height_str:
            use_custom_dim = True
            try:
                if width_str:
                    target_width = int(width_str)
                    if target_width <= 0:
                        raise ValueError
                if height_str:
                    target_height = int(height_str)
                    if target_height <= 0:
                        raise ValueError
            except ValueError:
                dlg = MsgDialog(
                    self,
                    "messages.warnings.invalid_dimensions.title",
                    "messages.warnings.invalid_dimensions.message",
                )
                dlg.exec()
                return

            if (self._same_dim and target_width and target_height
                    and target_width == self._cur_w and target_height == self._cur_h):
                self.accept()
                return

        scale_factor = 1.0
        if not use_custom_dim:
            checked = self._pct_group.checkedButton()
            if checked is None or checked.property("pct_type") == "zero":
                self.accept()
                return
            pct_type  = checked.property("pct_type")
            pct_value = checked.property("pct_value")
            if pct_type == "reduce":
                scale_factor = (100 - pct_value) / 100
            else:
                scale_factor = (100 + pct_value) / 100

        # ── Clustering / outliers (dimensions custom uniquement) ──────────────
        multi_page = (
            self._multi_page_cb.isChecked()
            if self._multi_page_cb is not None
            else False
        )

        all_widths  = []
        all_heights = []
        if use_custom_dim and multi_page and self._nb_files > 1:
            for entry in selected_entries:
                if entry.get("bytes"):
                    try:
                        tmp = Image.open(io.BytesIO(entry["bytes"]))
                        all_widths.append(tmp.width)
                        all_heights.append(tmp.height)
                        tmp.close()
                    except Exception:
                        pass

        reference_width  = None
        reference_height = None
        width_mapping    = {}
        height_mapping   = {}
        width_outliers   = set()
        height_outliers  = set()
        has_diff_widths  = False
        has_diff_heights = False

        if use_custom_dim and all_widths:
            reference_width,  width_mapping,  width_outliers  = cluster_and_find_reference(all_widths)
            reference_height, height_mapping, height_outliers = cluster_and_find_reference(all_heights)
            has_diff_widths  = len(set(all_widths))  > 1
            has_diff_heights = len(set(all_heights)) > 1

        # ── Dialogue outliers si nécessaire ───────────────────────────────────
        outlier_choices = {}
        if use_custom_dim and multi_page and (width_outliers or height_outliers):
            outlier_pages = []
            for entry in selected_entries:
                if not entry.get("bytes"):
                    continue
                try:
                    tmp = Image.open(io.BytesIO(entry["bytes"]))
                    hw  = tmp.width  in width_outliers
                    hh  = tmp.height in height_outliers
                    tmp.close()
                    if hw or hh:
                        outlier_pages.append({
                            "entry":             entry,
                            "has_width_outlier":  hw,
                            "has_height_outlier": hh,
                        })
                except Exception:
                    pass

            if outlier_pages:
                odlg = OutlierDialog(
                    self,
                    outlier_pages,
                    target_width, target_height,
                    reference_width, reference_height,
                    use_custom_dim,
                )
                odlg.exec()
                if odlg._result is None:
                    return   # annulé → on reste dans ResizeDialog
                outlier_choices = odlg._result

        # ── Ferme la fenêtre et lance le worker ───────────────────────────────
        self.accept()

        # Sauvegarde état avant
        save_state_fn()

        # Sauvegarde des bytes originaux pour restauration en cas d'annulation
        original_bytes = {id(e): e["bytes"] for e in selected_entries if e.get("bytes")}

        _start_resize_worker(
            canvas, selected_entries, state,
            use_custom_dim, target_width, target_height, scale_factor,
            multi_page, self._nb_files,
            has_diff_widths, has_diff_heights,
            reference_width, reference_height,
            width_mapping, height_mapping,
            outlier_choices,
            original_bytes,
            save_state_fn, render_mosaic_fn, update_button_text, refresh_status_fn,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Worker thread de resize
# ─────────────────────────────────────────────────────────────────────────────

class _ResizeWorker(QThread):
    progress  = Signal(int)
    finished  = Signal()
    cancelled = Signal()

    def __init__(self, selected_entries, state,
                 use_custom_dim, target_width, target_height, scale_factor,
                 multi_page, nb_files,
                 has_diff_widths, has_diff_heights,
                 reference_width, reference_height,
                 width_mapping, height_mapping,
                 outlier_choices):
        super().__init__()
        self._selected_entries = selected_entries
        self._state            = state
        self._use_custom_dim   = use_custom_dim
        self._target_width     = target_width
        self._target_height    = target_height
        self._scale_factor     = scale_factor
        self._multi_page       = multi_page
        self._nb_files         = nb_files
        self._has_diff_widths  = has_diff_widths
        self._has_diff_heights = has_diff_heights
        self._reference_width  = reference_width
        self._reference_height = reference_height
        self._width_mapping    = width_mapping
        self._height_mapping   = height_mapping
        self._outlier_choices  = outlier_choices
        self._cancelled        = threading.Event()

    def run(self):
        from PIL import Image
        state             = self._state
        selected_entries  = self._selected_entries
        use_custom_dim    = self._use_custom_dim
        target_width      = self._target_width
        target_height     = self._target_height
        scale_factor      = self._scale_factor
        multi_page        = self._multi_page
        nb_files          = self._nb_files
        has_diff_widths   = self._has_diff_widths
        has_diff_heights  = self._has_diff_heights
        reference_width   = self._reference_width
        reference_height  = self._reference_height
        width_mapping     = self._width_mapping
        height_mapping    = self._height_mapping
        outlier_choices   = self._outlier_choices
        total             = len(selected_entries)

        for idx, entry in enumerate(selected_entries):
            if self._cancelled.is_set():
                self.cancelled.emit()
                return
            try:
                if not entry.get("bytes"):
                    continue

                img = Image.open(io.BytesIO(entry["bytes"]))

                dpi_value = entry.get("dpi")
                if not dpi_value:
                    original_dpi = img.info.get("dpi", (72, 72))
                    dpi_value = original_dpi[0] if isinstance(original_dpi, tuple) else original_dpi
                elif isinstance(dpi_value, tuple):
                    dpi_value = dpi_value[0]

                original_jpeg_quality = detect_jpeg_quality(entry["bytes"])

                if use_custom_dim:
                    width_multiplier  = 1.0
                    height_multiplier = 1.0
                    page_name         = entry.get("orig_name", "")
                    user_choice       = outlier_choices.get(page_name)

                    if multi_page and nb_files > 1 and has_diff_widths and reference_width:
                        if user_choice and user_choice.get("width_mult") is not None:
                            um = user_choice["width_mult"]
                            width_multiplier = None if um == 0 else um
                        else:
                            width_multiplier = width_mapping.get(img.width, 1.0)

                    if multi_page and nb_files > 1 and has_diff_heights and reference_height:
                        if user_choice and user_choice.get("height_mult") is not None:
                            um = user_choice["height_mult"]
                            height_multiplier = None if um == 0 else um
                        else:
                            height_multiplier = height_mapping.get(img.height, 1.0)

                    if target_width and target_height:
                        if width_multiplier is None and height_multiplier is None:
                            img.close(); continue
                        elif width_multiplier is None:
                            new_w = img.width
                            new_h = int(target_height * height_multiplier)
                        elif height_multiplier is None:
                            new_w = int(target_width * width_multiplier)
                            new_h = img.height
                        else:
                            new_w = int(target_width  * width_multiplier)
                            new_h = int(target_height * height_multiplier)
                    elif target_width:
                        if width_multiplier is None:
                            img.close(); continue
                        new_w = int(target_width * width_multiplier)
                        new_h = int(img.height * (new_w / img.width))
                    elif target_height:
                        if height_multiplier is None:
                            img.close(); continue
                        new_h = int(target_height * height_multiplier)
                        new_w = int(img.width * (new_h / img.height))
                    else:
                        img.close(); continue
                else:
                    new_w = int(img.width  * scale_factor)
                    new_h = int(img.height * scale_factor)

                img_resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

                output = io.BytesIO()
                fmt = entry.get("orig_name", "").split(".")[-1].upper()
                if fmt not in ("JPEG", "JPG", "PNG", "WEBP", "BMP", "TIFF", "GIF"):
                    fmt = "JPEG"

                if fmt in ("JPEG", "JPG"):
                    img_resized.save(output, format="JPEG",
                                     quality=original_jpeg_quality,
                                     optimize=True, dpi=(dpi_value, dpi_value))
                elif fmt == "PNG":
                    img_resized.save(output, format="PNG",
                                     optimize=True, dpi=(dpi_value, dpi_value))
                elif fmt == "WEBP":
                    img_resized.save(output, format="WEBP",
                                     quality=original_jpeg_quality,
                                     dpi=(dpi_value, dpi_value))
                else:
                    img_resized.save(output, format=fmt, dpi=(dpi_value, dpi_value))

                entry["bytes"] = output.getvalue()
                entry["img"]   = None

                from modules.qt.comic_info import get_page_image_index, update_page_entries_in_xml_data
                _pidx = get_page_image_index(state, entry)
                if _pidx is not None:
                    update_page_entries_in_xml_data(state, [(_pidx, entry)], emit_signal=False)

                img.close()
                img_resized.close()
                del img, img_resized, output

                if entry.get("large_thumb_pil") is not None:
                    entry["large_thumb_pil"].close()
                    entry["large_thumb_pil"] = None

                entry.pop("qt_pixmap_large", None)
                entry.pop("qt_qimage_large", None)
                build_qimage_for_entry(entry)   # pré-calcule la miniature dans le thread bg
                free_image_memory(entry)

            except Exception:
                pass

            self.progress.emit(int((idx + 1) / total * 100))

        self.finished.emit()


def _start_resize_worker(canvas, selected_entries, state,
                         use_custom_dim, target_width, target_height, scale_factor,
                         multi_page, nb_files,
                         has_diff_widths, has_diff_heights,
                         reference_width, reference_height,
                         width_mapping, height_mapping,
                         outlier_choices,
                         original_bytes,
                         save_state_fn, render_mosaic_fn, update_button_text, refresh_status_fn):
    from modules.qt.canvas_overlay_qt import show_canvas_text as _show_ct, hide_canvas_text as _hide_ct
    from modules.qt.web_import_qt import _show_cancel_item

    item_holder        = [None]
    cancel_holder      = [None]
    worker_ref         = [None]
    modified_before    = state.modified   # sauvegarde pour restauration si annulation

    def _show(pct):
        if worker_ref[0] is None:
            return
        _show_ct(canvas, _("labels.resizing", percent=pct), item_holder)
        _show_cancel_item(canvas, f"[ {_('buttons.cancel')} ]", cancel_holder, _cancel,
                          anchor_lbl=item_holder[0])

    def _hide():
        _hide_ct(canvas, item_holder)
        _hide_ct(canvas, cancel_holder)

    def _cancel():
        w = worker_ref[0]
        if w is None:
            return
        w._cancelled.set()
        worker_ref[0] = None
        _hide()
        # Restaure les bytes originaux pour toutes les entrées déjà modifiées
        for entry in selected_entries:
            orig = original_bytes.get(id(entry))
            if orig is not None:
                entry["bytes"] = orig
                entry["img"] = None
                if entry.get("large_thumb_pil") is not None:
                    entry["large_thumb_pil"].close()
                    entry["large_thumb_pil"] = None
                entry.pop("qt_pixmap_large", None)
                entry.pop("qt_qimage_large", None)
                free_image_memory(entry)
        # Dépile l'état undo poussé avant le lancement du worker
        from modules.qt.undo_redo import pop_last_state
        pop_last_state(state)
        # Restaure l'état modifié d'avant le resize
        state.modified = modified_before
        update_button_text()

    def on_progress(pct):
        _show(pct)

    def on_finished():
        if worker_ref[0] is None:
            return   # annulé entre-temps
        worker_ref[0] = None
        _hide()
        state.modified = True
        from modules.qt.metadata_signal import metadata_pages_signal
        metadata_pages_signal.emit()
        for entry in selected_entries:
            real_idx = entry.get("_real_idx")
            if real_idx is not None:
                canvas.refresh_thumbnail(real_idx)
        update_button_text()
        refresh_status_fn()
        save_state_fn()

    def on_cancelled():
        # Nettoyage déjà fait dans _cancel
        pass

    def _cleanup():
        worker.deleteLater()

    worker = _ResizeWorker(
        selected_entries, state,
        use_custom_dim, target_width, target_height, scale_factor,
        multi_page, nb_files,
        has_diff_widths, has_diff_heights,
        reference_width, reference_height,
        width_mapping, height_mapping,
        outlier_choices,
    )
    worker_ref[0] = worker
    worker.progress.connect(on_progress)
    worker.finished.connect(on_finished)
    worker.cancelled.connect(on_cancelled)
    worker.finished.connect(_cleanup)
    worker.cancelled.connect(_cleanup)
    _show(0)
    worker.start()


# ─────────────────────────────────────────────────────────────────────────────
# Point d'entrée public
# ─────────────────────────────────────────────────────────────────────────────

def reduce_selected_images_size_qt(parent, callbacks: dict):
    """
    Ouvre le dialogue de redimensionnement.

    callbacks attendus :
        save_state        : callable
        render_mosaic     : callable
        update_button_text: callable
        canvas            : MosaicCanvas  — pour afficher le texte de progression rouge
    """
    from modules.qt.dialogs_qt import MsgDialog as _MsgDialog

    state = callbacks.get('state') or _state_module.state

    if not state.selected_indices:
        dlg = _MsgDialog(
            parent,
            "messages.warnings.no_selection_reduce.title",
            "messages.warnings.no_selection_reduce.message",
        )
        dlg.exec()
        return

    selected_entries = [
        state.images_data[idx]
        for idx in sorted(state.selected_indices)
        if idx < len(state.images_data) and state.images_data[idx].get("is_image")
    ]
    if not selected_entries:
        dlg = _MsgDialog(
            parent,
            "messages.warnings.invalid_selection_reduce.title",
            "messages.warnings.invalid_selection_reduce.message",
        )
        dlg.exec()
        return

    dlg = ResizeDialog(parent, selected_entries, callbacks)
    dlg.exec()
