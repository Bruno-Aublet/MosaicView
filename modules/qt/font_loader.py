#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Module de chargement des polices personnalisées pour MosaicView (Qt)
"""

import os
import sys
import platform


def resource_path(relative_path):
    """Obtient le chemin absolu vers une ressource (compatible PyInstaller)"""
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


FONTS_DIR = resource_path('fonts')

PIQAD_FONT_FILE = 'pIqaD-qolqoS.ttf'
TENGWAR_FONT_FILES = ['AlcarinTengwarVF.ttf']
TENGWAR_FONT_FILE = 'AlcarinTengwarVF.ttf'


def load_custom_font_windows(font_path):
    try:
        import ctypes
        from ctypes import wintypes
        gdi32 = ctypes.WinDLL('gdi32', use_last_error=True)
        AddFontResourceEx = gdi32.AddFontResourceExW
        AddFontResourceEx.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID]
        AddFontResourceEx.restype = ctypes.c_int
        return AddFontResourceEx(font_path, 0x10, 0) > 0
    except Exception as e:
        print(f"Erreur chargement police {font_path}: {e}")
        return False


def load_custom_font(font_path):
    if not os.path.exists(font_path):
        return False
    if platform.system() == 'Windows':
        return load_custom_font_windows(font_path)
    return False


def init_font_manager():
    """Charge toutes les polices personnalisées (pIqaD + Tengwar)."""
    piqad_path = os.path.join(FONTS_DIR, PIQAD_FONT_FILE)
    load_custom_font(piqad_path)
    for f in TENGWAR_FONT_FILES:
        load_custom_font(os.path.join(FONTS_DIR, f))
