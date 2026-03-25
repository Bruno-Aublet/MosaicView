# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all, collect_data_files

# Données à inclure dans l'exécutable
datas = [
    ('icons', 'icons'),
    ('paypal', 'paypal'),
    ('locales', 'locales'),
    ('fonts', 'fonts'),
    ('LICENSE', '.'),
    ('icons/MosaicView.ico', 'icons'),
    ('unrar/UnRAR.exe', 'unrar'),
    ('unrar/license.txt', 'unrar'),
    ('7zip/7z.exe', '7zip'),
    ('7zip/7z.dll', '7zip'),
    ('7zip/license.txt', '7zip'),
]

binaries = []

hiddenimports = [
    # PIL/Pillow (sans ImageTk — pas de tkinter)
    'PIL', 'PIL.Image', 'PIL.ImageDraw', 'PIL.ImageFont',
    'PIL._imaging',
    'PIL.ImageEnhance', 'PIL.ImageFilter', 'PIL.ImageOps',
    'PIL.JpegImagePlugin', 'PIL.PngImagePlugin', 'PIL.GifImagePlugin',
    'PIL.WebPImagePlugin', 'PIL.BmpImagePlugin', 'PIL.TiffImagePlugin',
    'PIL.IcoImagePlugin', 'PIL.JpegPresets',

    # Modules standards
    'json', 'locale', 'xml.etree.ElementTree',
    'platform', 'ctypes', 'ctypes.wintypes',
    'send2trash',

    # PySide6
    'PySide6', 'PySide6.QtWidgets', 'PySide6.QtGui', 'PySide6.QtCore',
    'PySide6.QtPrintSupport',

    'modules.qt',

    # Modules Qt
    'modules.qt',
    'modules.qt.mosaic_canvas',
    'modules.qt.icon_toolbar_qt',
    'modules.qt.tabs_qt',
    'modules.qt.toggle_theme_qt',
    'modules.qt.context_menus_qt',
    'modules.qt.menubar_qt',
    'modules.qt.archive_loader',
    'modules.qt.undo_redo_qt',
    'modules.qt.sorting_qt',
    'modules.qt.flatten_directories_qt',
    'modules.qt.font_manager_qt',
    'modules.qt.status_bar_qt',
    'modules.qt.language_signal',
    'modules.qt.import_merge_qt',
    'modules.qt.session_restore_qt',
    'modules.qt.wheel_hook',
    'modules.qt.pdf_unlock_qt',
    'modules.qt.pdf_loading_qt',
    'modules.qt.license_dialog_qt',
    'modules.qt.file_operations_qt',
    'modules.qt.image_transforms_qt',
    'modules.qt.renumbering_qt',
    'modules.qt.file_close_qt',
    'modules.qt.batch_drop_dialog_qt',
    'modules.qt.drop_handler_qt',
    'modules.qt.batch_dialogs_qt',
    'modules.qt.user_guide_qt',
    'modules.qt.tooltips_qt',
    'modules.qt.dialogs_qt',
    # Modules partagés déplacés dans modules/qt/
    'modules.qt.config_manager',
    'modules.qt.localization',
    'modules.qt.state',
    'modules.qt.entries',
    'modules.qt.undo_redo',
    'modules.qt.recent_files',
    'modules.qt.utils',
    'modules.qt.comic_info',
    'modules.qt.image_ops',
    'modules.qt.sorting',
    'modules.qt.font_loader',
    'modules.qt.renumbering',
    'modules.qt.temp_files',
    'modules.qt.ico_creator_qt',
    'modules.qt.clipboard_qt',
    'modules.qt.window_title_qt',
    'modules.qt.split_dialog_qt',
    'modules.qt.image_viewer_qt',
    'modules.qt.conversion_dialogs_qt',
    'modules.qt.menubar_callbacks_qt',
    'modules.qt.animated_gif_dialog_qt',
    'modules.qt.open_with_default_app_qt',
    'modules.qt.non_image_sorting',
    'modules.qt.resize_dialog_qt',
    'modules.qt.merge_dialog_qt',
    'modules.qt.canvas_overlay_qt',
    'modules.qt.overlay_tooltip_qt',
    'modules.qt.adjustments_dialog_qt',
    'modules.qt.adjustments_processing_qt',
    'modules.qt.adjustments_viewers_qt',
    'modules.qt.web_import_qt',
    'modules.qt.keyboard_nav_qt',
    'modules.qt.metadata_signal',
    'modules.qt.printing_qt',
    'modules.qt.donation_dialog_qt',
    'modules.qt.panel_widget',

    # Optionnels
    'fitz',
    'rarfile',
    'win32clipboard', 'win32print', 'win32ui', 'win32con',
    'numpy', 'tifffile', 'olefile', 'defusedxml',
]

tmp_ret = collect_all('numpy')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# numpy.libs — DLLs indispensables non collectées automatiquement
import glob as _glob
_numpy_libs = _glob.glob('.venv/Lib/site-packages/numpy.libs/*.dll')
binaries += [(_dll, 'numpy.libs') for _dll in _numpy_libs]


tmp_ret = collect_all('PIL')
# Exclure PIL.ImageTk qui importe tkinter
_pil_datas    = [(s, d) for s, d in tmp_ret[0] if 'ImageTk' not in s]
_pil_binaries = [(s, d) for s, d in tmp_ret[1] if 'ImageTk' not in s]
datas += _pil_datas; binaries += _pil_binaries; hiddenimports += tmp_ret[2]

# PySide6 — collecte ciblée sur les 3 modules utilisés uniquement
import os as _os

_qt_used = ['PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets', 'PySide6.QtPrintSupport']
for _mod in _qt_used:
    try:
        _r = collect_all(_mod)
        datas += _r[0]; binaries += _r[1]; hiddenimports += _r[2]
    except Exception:
        pass

# Binaires PySide6 racine nécessaires au démarrage (pyside6.abi3.dll, Qt6Core.dll, etc.)
# récupérés manuellement depuis le répertoire PySide6
try:
    import PySide6 as _ps6
    _ps6_dir = _os.path.dirname(_ps6.__file__)
    _qt_core_dlls = [
        'pyside6.abi3.dll', 'Qt6Core.dll', 'Qt6Gui.dll', 'Qt6Widgets.dll',
        'Qt6PrintSupport.dll', 'Qt6DBus.dll',
        'msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll',
        'msvcp140_codecvt_ids.dll', 'concrt140.dll', 'vcruntime140.dll',
        'vcruntime140_1.dll', 'opengl32sw.dll',
    ]
    for _dll in _qt_core_dlls:
        _p = _os.path.join(_ps6_dir, _dll)
        if _os.path.exists(_p):
            binaries.append((_p, 'PySide6'))
    # Plugins Qt indispensables
    _plugins_needed = ['platforms', 'styles', 'imageformats']
    _plugins_dir = _os.path.join(_ps6_dir, 'plugins')
    if _os.path.exists(_plugins_dir):
        for _plugin in _plugins_needed:
            _pd = _os.path.join(_plugins_dir, _plugin)
            if _os.path.exists(_pd):
                for _f in _os.listdir(_pd):
                    if _f.endswith('.dll'):
                        binaries.append((_os.path.join(_pd, _f), _os.path.join('PySide6', 'plugins', _plugin)))
except Exception as _e:
    print(f"Avertissement PySide6 binaries: {_e}")

try:
    tmp_ret = collect_all('fitz')
    datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
except:
    pass

try:
    tmp_ret = collect_all('rarfile')
    datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
except:
    pass

try:
    tmp_ret = collect_all('send2trash')
    datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
except:
    pass

a = Analysis(
    ['MosaicView.py'],
    pathex=['.venv/Lib/site-packages'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'tkinterdnd2', 'PIL.ImageTk', 'PIL._tkinter_finder',
        'pandas', 'matplotlib', 'scipy', 'IPython', 'notebook', 'pytest',
        'pkg_resources', 'setuptools', 'jaraco',
        # PySide6 — modules non utilisés
        'PySide6.Qt3DAnimation', 'PySide6.Qt3DCore', 'PySide6.Qt3DExtras',
        'PySide6.Qt3DInput', 'PySide6.Qt3DLogic', 'PySide6.Qt3DRender',
        'PySide6.QtBluetooth', 'PySide6.QtCharts', 'PySide6.QtConcurrent',
        'PySide6.QtDataVisualization', 'PySide6.QtDesigner',
        'PySide6.QtHelp', 'PySide6.QtLocation', 'PySide6.QtMultimedia',
        'PySide6.QtMultimediaWidgets', 'PySide6.QtNetwork', 'PySide6.QtNetworkAuth',
        'PySide6.QtNfc', 'PySide6.QtOpenGL', 'PySide6.QtOpenGLWidgets',
        'PySide6.QtPositioning', 'PySide6.QtQml', 'PySide6.QtQuick',
        'PySide6.QtQuick3D', 'PySide6.QtQuickControls2', 'PySide6.QtQuickWidgets',
        'PySide6.QtRemoteObjects', 'PySide6.QtScxml', 'PySide6.QtSensors',
        'PySide6.QtSerialBus', 'PySide6.QtSerialPort', 'PySide6.QtSpatialAudio',
        'PySide6.QtSql', 'PySide6.QtStateMachine', 'PySide6.QtSvg',
        'PySide6.QtSvgWidgets', 'PySide6.QtTest', 'PySide6.QtTextToSpeech',
        'PySide6.QtUiTools', 'PySide6.QtWebChannel', 'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineQuick', 'PySide6.QtWebEngineWidgets',
        'PySide6.QtWebSockets', 'PySide6.QtXml',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

splash = Splash(
    'icons/splash.png',
    binaries=a.binaries,
    datas=a.datas,
    text_pos=None,
    text_size=0,
    minify_script=True,
    always_on_top=True,
)

exe = EXE(
    pyz,
    a.scripts,
    splash,
    [],
    exclude_binaries=True,
    name='MosaicView',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[
        'unrar/UnRAR.exe', '7zip/7z.exe', '7zip/7z.dll',
        # numpy — UPX corrompt ses binaires
        '_multiarray_umath.cp311-win_amd64.pyd',
        '_multiarray_tests.cp311-win_amd64.pyd',
        '_umath_tests.cp311-win_amd64.pyd',
        '_simd.cp311-win_amd64.pyd',
        'mtrand.cp311-win_amd64.pyd',
        '_pocketfft_umath.cp311-win_amd64.pyd',
        'libscipy_openblas64_-860d95b1c38e637ce4509f5fa24fbf2a.dll',
        'msvcp140-a4c2229bdc2a2a630acdc095b4d86008.dll',
    ],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icons/Icone_exe.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    splash.binaries,
    strip=False,
    upx=True,
    upx_exclude=[
        'unrar/UnRAR.exe', '7zip/7z.exe', '7zip/7z.dll',
        '_multiarray_umath.cp311-win_amd64.pyd',
        '_multiarray_tests.cp311-win_amd64.pyd',
        '_umath_tests.cp311-win_amd64.pyd',
        '_simd.cp311-win_amd64.pyd',
        'mtrand.cp311-win_amd64.pyd',
        '_pocketfft_umath.cp311-win_amd64.pyd',
        'libscipy_openblas64_-860d95b1c38e637ce4509f5fa24fbf2a.dll',
        'msvcp140-a4c2229bdc2a2a630acdc095b4d86008.dll',
    ],
    name='MosaicView',
)
