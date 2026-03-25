"""
modules/qt/font_manager_qt.py
Chargement des polices spéciales (pIqaD / Tengwar) via QFontDatabase.

Usage :
    from modules.qt.font_manager_qt import FontManagerQt
    fm = FontManagerQt()
    fm.load_fonts()
    qfont_piqad   = fm.get_piqad_font(size=9)
    qfont_tengwar = fm.get_tengwar_font(size=9)
"""

import os

from PySide6.QtGui import QFont, QFontDatabase

from modules.qt.font_loader import resource_path


_PIQAD_FILE        = 'pIqaD-qolqoS.ttf'
_TENGWAR_FILES     = ['AlcarinTengwarVF.ttf']
_PIQAD_FALLBACKS   = ['pIqaD qolqoS', 'KApIqaD', 'Code2000', 'Constructium']
_TENGWAR_FALLBACKS = ['Alcarin Tengwar', 'Tengwar Annatar', 'Tengwar Telcontar', 'Tengwar Formal']


class FontManagerQt:
    """Gère le chargement et l'accès aux polices pIqaD et Tengwar pour Qt."""

    def __init__(self):
        self.piqad_font_name:   str | None = None
        self.tengwar_font_name: str | None = None
        self._loaded = False

    def load_fonts(self):
        """Charge les polices depuis le dossier fonts/ via QFontDatabase."""
        fonts_dir = resource_path('fonts')

        # pIqaD
        piqad_path = os.path.join(fonts_dir, _PIQAD_FILE)
        if os.path.isfile(piqad_path):
            fid = QFontDatabase.addApplicationFont(piqad_path)
            if fid >= 0:
                fams = QFontDatabase.applicationFontFamilies(fid)
                if fams:
                    self.piqad_font_name = fams[0]

        # Fallback système pIqaD
        if not self.piqad_font_name:
            available = QFontDatabase.families()
            for name in _PIQAD_FALLBACKS:
                if name in available:
                    self.piqad_font_name = name
                    break

        # Tengwar
        for tfile in _TENGWAR_FILES:
            tpath = os.path.join(fonts_dir, tfile)
            if os.path.isfile(tpath):
                fid = QFontDatabase.addApplicationFont(tpath)
                if fid >= 0 and self.tengwar_font_name is None:
                    fams = QFontDatabase.applicationFontFamilies(fid)
                    if fams:
                        self.tengwar_font_name = fams[0]

        # Fallback système Tengwar
        if not self.tengwar_font_name:
            available = QFontDatabase.families()
            for name in _TENGWAR_FALLBACKS:
                if name in available:
                    self.tengwar_font_name = name
                    break

        self._loaded = True

    def get_piqad_font(self, size: int = 9) -> QFont:
        family = self.piqad_font_name or 'Arial'
        return QFont(family, size)

    def get_tengwar_font(self, size: int = 9) -> QFont:
        family = self.tengwar_font_name or 'Arial'
        return QFont(family, size)


# Instance globale (initialisée dans MainWindow.__init__ via load_fonts())
_font_manager: FontManagerQt | None = None


def init_font_manager() -> FontManagerQt:
    global _font_manager
    _font_manager = FontManagerQt()
    _font_manager.load_fonts()
    return _font_manager


def get_font_manager() -> FontManagerQt | None:
    return _font_manager


def get_current_font(size: int = 10, family: str = "Arial", bold: bool = False) -> QFont:
    """
    Équivalent Qt de modules/fonts.py:get_current_font().
    Retourne le bon QFont selon la langue active :
      - tlh-piqad          → police pIqaD
      - sjn-tengwar / qya-tengwar → police Tengwar
      - toutes autres langues     → family (Arial par défaut)
    L'offset de taille de police (config) est appliqué.
    """
    from modules.qt.localization import get_localization
    from modules.qt.config_manager import get_config_manager

    loc    = get_localization()
    config = get_config_manager()
    fm     = _font_manager

    current_lang = loc.get_current_language() if loc else ""
    offset       = config.get_font_size_offset() if config else 0
    adjusted     = max(1, size + offset)

    if current_lang == 'tlh-piqad' and fm and fm.piqad_font_name:
        f = QFont(fm.piqad_font_name, adjusted)
    elif current_lang in ('sjn-tengwar', 'qya-tengwar') and fm and fm.tengwar_font_name:
        f = QFont(fm.tengwar_font_name, adjusted)
    else:
        f = QFont(family, adjusted)

    if bold:
        f.setBold(True)
    return f
