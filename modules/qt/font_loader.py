#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Module de chargement des polices personnalisées pour MosaicView (Qt)
"""

import os
import sys


def resource_path(relative_path):
    """Obtient le chemin absolu vers une ressource (compatible PyInstaller)"""
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


PIQAD_FONT_FILE = 'pIqaD-qolqoS.ttf'
TENGWAR_FONT_FILES = ['AlcarinTengwarVF.ttf']
