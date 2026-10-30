"""
modules/qt/wheel_hook.py
Hook souris bas niveau Win32 (WH_MOUSE_LL) pour router WM_MOUSEWHEEL
vers un QComboBox même quand une autre fenêtre Qt est active.
"""

import ctypes
import ctypes.wintypes
import threading

from PySide6.QtCore import QObject, Signal, QTimer

WH_MOUSE_LL   = 14
WM_MOUSEWHEEL = 0x020A
HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, ctypes.c_int,
                               ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)

_CallNextHookEx = ctypes.windll.user32.CallNextHookEx
_CallNextHookEx.restype  = ctypes.c_longlong
_CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int,
                             ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]

class _MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt",          ctypes.wintypes.POINT),
        ("mouseData",   ctypes.wintypes.DWORD),
        ("flags",       ctypes.wintypes.DWORD),
        ("time",        ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


# Instance propre à ce module : poser restype/argtypes sur ctypes.windll.user32
# changerait ces fonctions pour tout le process.
_user32 = ctypes.WinDLL("user32")
_user32.WindowFromPoint.restype  = ctypes.wintypes.HWND
_user32.WindowFromPoint.argtypes = [ctypes.wintypes.POINT]
_user32.GetAncestor.restype      = ctypes.wintypes.HWND
_user32.GetAncestor.argtypes     = [ctypes.wintypes.HWND, ctypes.wintypes.UINT]
GA_ROOT = 2


def _root_window_at(gx, gy):
    """Fenêtre de premier niveau réellement visible sous ce point écran (vrai
    Z-order de Windows), ou None. QApplication.widgetAt() ne connaît que les
    fenêtres Qt : une fenêtre d'un autre programme, ou une fenêtre native ouverte
    par l'appli elle-même dans son process (boîte Ouvrir, assistant d'impression,
    pilote de scanner), lui échappe."""
    hwnd = _user32.WindowFromPoint(ctypes.wintypes.POINT(gx, gy))
    if not hwnd:
        return None
    return _user32.GetAncestor(hwnd, GA_ROOT) or hwnd


class WheelHook:
    """
    Hook souris global Win32. Quand WM_MOUSEWHEEL est détecté et que la souris
    est sur l'un des targets enregistrés, simule un wheelEvent Qt sur ce widget.
    """

    def __init__(self, target):
        self._targets = [target]
        self._hook    = None
        self._proc    = None
        self._thread  = None
        # File thread-safe pour passer les données au thread Qt
        self._pending = []
        self._lock    = threading.Lock()
        # Défilement fin (pavé tactile : crans inférieurs à 120) cumulé jusqu'à
        # un cran entier, dans un sens comme dans l'autre.
        self._wheel_accum = 0
        # Timer Qt pour drainer la file dans le thread principal
        self._timer = QTimer()
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._drain)

    def install(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._timer.start()

    def uninstall(self):
        self._timer.stop()
        if self._thread is not None:
            ctypes.windll.user32.PostThreadMessageW(
                self._thread.ident, 0x0012, 0, 0  # WM_QUIT
            )

    # ── thread du hook — doit être ULTRA rapide ───────────────────────────────
    def _run(self):
        def hook_proc(nCode, wParam, lParam):
            if nCode >= 0 and wParam == WM_MOUSEWHEEL:
                ms    = ctypes.cast(lParam, ctypes.POINTER(_MSLLHOOKSTRUCT)).contents
                delta = ctypes.c_short(ms.mouseData >> 16).value
                with self._lock:
                    self._pending.append((ms.pt.x, ms.pt.y, delta))
            return _CallNextHookEx(self._hook, nCode, wParam, lParam)

        self._proc = HOOKPROC(hook_proc)
        self._hook = ctypes.windll.user32.SetWindowsHookExW(
            WH_MOUSE_LL, self._proc, None, 0
        )

        msg = ctypes.wintypes.MSG()
        while True:
            r = ctypes.windll.user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r == 0 or r == -1:
                break
            ctypes.windll.user32.TranslateMessage(ctypes.byref(msg))
            ctypes.windll.user32.DispatchMessageW(ctypes.byref(msg))

        if self._hook:
            ctypes.windll.user32.UnhookWindowsHookEx(self._hook)
            self._hook = None

    # ── thread Qt — traite les événements en attente ──────────────────────────
    def _drain(self):
        with self._lock:
            events = self._pending[:]
            self._pending.clear()
        for gx, gy, delta in events:
            try:
                self._dispatch(gx, gy, delta)
            except RuntimeError:
                # Widget C++ déjà détruit (ex: panel2 fermé)
                pass

    def register(self, target):
        if target not in self._targets:
            self._targets.append(target)

    def unregister(self, target):
        try:
            self._targets.remove(target)
        except ValueError:
            pass

    def _dispatch(self, gx, gy, delta):
        from PySide6.QtCore import QPoint, QPointF, Qt
        from PySide6.QtGui import QWheelEvent
        from PySide6.QtWidgets import QApplication
        gpos = QPoint(gx, gy)
        # Fenêtre modale ApplicationModal : bloquer tout
        widget_at = QApplication.widgetAt(gpos)
        if widget_at is not None:
            top = widget_at.window()
            if top is not None and top.windowModality() == Qt.ApplicationModal:
                return
        for target in list(self._targets):
            if target is None or not target.isVisible():
                continue
            lpos = target.mapFromGlobal(gpos)
            if not target.rect().contains(lpos):
                continue
            # Toute autre fenêtre posée sur le combo (autre programme, fenêtre
            # native ou Qt de l'appli, liste déroulante) garde sa molette. Les
            # tooltips (OverlayTooltip) sont des enfants sans fenêtre Windows
            # propre : ils ne changent pas la fenêtre racine.
            if _root_window_at(gx, gy) != int(target.window().winId()):
                continue
            # int() tronque vers zéro, contrairement à // : un petit
            # mouvement compte autant vers le haut que vers le bas.
            self._wheel_accum += delta
            steps = int(self._wheel_accum / 120)
            self._wheel_accum -= steps * 120
            if steps == 0:
                break
            angle = QPoint(0, steps * 120)
            event = QWheelEvent(
                QPointF(lpos), QPointF(gpos),
                QPoint(0, 0), angle,
                Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False
            )
            if hasattr(target, 'wheel_from_hook'):
                target.wheel_from_hook(event)
            else:
                QApplication.sendEvent(target, event)
            break  # Un seul target par événement (la souris ne peut être que sur un)
