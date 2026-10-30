"""
window_frame_qt.py — Cadre Windows d'une fenêtre, sans sa barre de titre.

La fenêtre garde le comportement du vrai cadre de Windows (ombre, animations
de réduction et d'agrandissement, ancrage contre les bords de l'écran) ; seule
la zone du titre est rendue à la fenêtre Qt, qui y dessine titre et boutons :
`TitleBar` (title_bar_qt.py) pour la fenêtre principale, `FramedWindowMixin`
(framed_window_qt.py) pour les autres fenêtres.

Principe :
- la fenêtre est créée sans cadre (`Qt.FramelessWindowHint`) : Qt considère
  alors toute la fenêtre comme sa zone cliente, ce qui garde ses calculs de
  position justes ; `install` rend ensuite à la fenêtre Windows les styles du
  cadre (WS_CAPTION, WS_THICKFRAME…), dont dépend le comportement natif ;
- WM_NCCALCSIZE : toute la fenêtre est zone cliente, Windows ne dessine ni
  barre de titre ni bord ; une fenêtre agrandie est ramenée à la zone de
  travail de son écran, que Windows déborde de l'épaisseur du cadre ;
- WM_NCHITTEST : les bords redimensionnent, la barre de titre est déclarée
  barre de titre à Windows (déplacement, double-clic qui agrandit, glisser
  contre un bord de l'écran) ; ses boutons restent des widgets Qt.

Appelé par `MainWindow.nativeEvent` (MosaicView.py) et
`FramedWindowMixin.nativeEvent` ; skill `window-title`.
"""

import ctypes
import sys
from ctypes import wintypes

WM_NCCALCSIZE = 0x0083
WM_NCHITTEST = 0x0084
WM_NCRBUTTONUP = 0x00A5
WM_SYSKEYDOWN = 0x0104
WM_SYSCHAR = 0x0106
WM_SYSCOMMAND = 0x0112
VK_SPACE = 0x20

HTCAPTION = 2
HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT = 10, 11, 12, 13, 14
HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 15, 16, 17

SC_MINIMIZE = 0xF020
SC_MAXIMIZE = 0xF030
SC_RESTORE = 0xF120

GWL_STYLE = -16
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020

SW_SHOWMAXIMIZED = 3
MONITOR_DEFAULTTONEAREST = 2
WVR_REDRAW = 0x0300

ABM_GETSTATE = 4
ABM_GETAUTOHIDEBAREX = 11
ABS_AUTOHIDE = 1
ABE_LEFT, ABE_TOP, ABE_RIGHT, ABE_BOTTOM = 0, 1, 2, 3

# Largeur des bords qui redimensionnent, en pixels physiques : ils sont pris
# sur la fenêtre elle-même, Windows n'ayant plus de bord invisible autour.
RESIZE_BORDER = 5
# Bande laissée libre contre une barre des tâches masquée automatiquement :
# une fenêtre agrandie qui couvre tout l'écran l'empêcherait de réapparaître.
AUTO_HIDE_GAP = 2


class NCCALCSIZE_PARAMS(ctypes.Structure):
    _fields_ = [("rgrc", wintypes.RECT * 3), ("lppos", ctypes.c_void_p)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [("length", wintypes.UINT), ("flags", wintypes.UINT),
                ("showCmd", wintypes.UINT), ("ptMinPosition", wintypes.POINT),
                ("ptMaxPosition", wintypes.POINT), ("rcNormalPosition", wintypes.RECT)]


class APPBARDATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                ("uCallbackMessage", wintypes.UINT), ("uEdge", wintypes.UINT),
                ("rc", wintypes.RECT), ("lParam", wintypes.LPARAM)]


class MARGINS(ctypes.Structure):
    _fields_ = [("cxLeftWidth", ctypes.c_int), ("cxRightWidth", ctypes.c_int),
                ("cyTopHeight", ctypes.c_int), ("cyBottomHeight", ctypes.c_int)]


if sys.platform == "win32":
    # Instances propres à ce module : les types d'arguments déclarés ici ne
    # touchent pas aux appels de ctypes.windll faits ailleurs dans l'application.
    _user32 = ctypes.WinDLL("user32")
    _dwmapi = ctypes.WinDLL("dwmapi")
    _shell32 = ctypes.WinDLL("shell32")

    _user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    _user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    _user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    _user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, wintypes.UINT]
    _user32.GetWindowPlacement.argtypes = [wintypes.HWND, ctypes.POINTER(WINDOWPLACEMENT)]
    _user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    _user32.MonitorFromWindow.restype = wintypes.HMONITOR
    _user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
    _user32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    _user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _dwmapi.DwmExtendFrameIntoClientArea.argtypes = [wintypes.HWND, ctypes.POINTER(MARGINS)]
    _shell32.SHAppBarMessage.argtypes = [wintypes.DWORD, ctypes.POINTER(APPBARDATA)]
    _shell32.SHAppBarMessage.restype = ctypes.c_size_t


def _hwnd(window) -> int:
    """Handle Windows de la fenêtre, créée au besoin comme le ferait son
    affichage. Jamais winId() : il marque la fenêtre native
    (WA_NativeWindow), et pour une fenêtre dont le parent est un widget (un
    dialogue ouvert sur un panneau), Qt rend alors natifs ce widget, ses
    ancêtres et ses enfants — un menu ouvert ensuite sur le panneau s'y
    rattache et Qt le refuse (« must be a top level window »)."""
    window.create()
    return int(window.effectiveWinId())


def caption_buttons(is_window_type, minimize_hint, maximize_hint, has_parent,
                    fixed_width, fixed_height):
    """(réduire, agrandir) : boutons d'une barre de titre dessinée par Qt,
    fidèles à ceux que Windows affichait.

    `is_window_type` : fenêtre de type `Qt.Window` (Windows lui donnait
    réduire et agrandir), et non `Qt.Dialog` (fermer seul) ; `*_hint` :
    bouton demandé explicitement par les drapeaux de la fenêtre. Pas de
    réduire pour une fenêtre qui a une fenêtre parente : Windows la réduirait
    en une petite barre au-dessus de la barre des tâches, sans bouton dans
    celle-ci. Pas d'agrandir pour une fenêtre dont une dimension est fixe.
    Fermer est toujours présent."""
    minimize = (is_window_type or minimize_hint) and not has_parent
    maximize = (is_window_type or maximize_hint) and not (fixed_width or fixed_height)
    return minimize, maximize


def install(window, minimize=True, maximize=True, resizable=True) -> bool:
    """Rend à la fenêtre (déjà marquée `Qt.FramelessWindowHint`) les styles du
    cadre Windows et son ombre. À appeler une fois, avant le premier affichage.
    `minimize`/`maximize` : boutons de la fenêtre (les styles correspondants
    décident aussi du double-clic sur la barre et de l'agrandissement par
    glisser contre le haut de l'écran) ; `resizable` : faux pour une fenêtre
    de taille entièrement fixe (ni bord épais ni ancrage qui la redimensionne).
    Retourne False hors de Windows : la fenêtre reste alors sans cadre."""
    if sys.platform != "win32":
        return False
    hwnd = _hwnd(window)
    style = _user32.GetWindowLongPtrW(hwnd, GWL_STYLE)
    style &= ~(WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX)
    style |= WS_CAPTION
    if resizable:
        style |= WS_THICKFRAME
    if minimize:
        style |= WS_MINIMIZEBOX
    if maximize:
        style |= WS_MAXIMIZEBOX
    _user32.SetWindowLongPtrW(hwnd, GWL_STYLE, style)
    # Cadre étendu à toute la fenêtre : Windows dessine son ombre ; la
    # fenêtre Qt, opaque, recouvre le reste.
    margins = MARGINS(-1, -1, -1, -1)
    _dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))
    _refresh_frame(hwnd)
    # Changement d'écran (mise à l'échelle différente) : Windows recalcule la
    # zone cliente seulement si on le lui demande.
    window.windowHandle().screenChanged.connect(
        lambda _screen: _refresh_frame(_hwnd(window)))
    return True


def _refresh_frame(hwnd: int) -> None:
    """Fait renvoyer WM_NCCALCSIZE par Windows, sans déplacer la fenêtre."""
    _user32.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                         SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER
                         | SWP_NOACTIVATE | SWP_FRAMECHANGED)


def minimize(window) -> None:
    """Réduit la fenêtre comme le bouton de Windows (avec son animation)."""
    _user32.PostMessageW(_hwnd(window), WM_SYSCOMMAND, SC_MINIMIZE, 0)


def toggle_maximized(window) -> None:
    """Agrandit ou restaure la fenêtre par la même commande que le bouton de
    Windows (animation, ancrage), et non par showMaximized/showNormal de Qt,
    qui ignore les styles de cadre rendus par `install`."""
    command = SC_RESTORE if window.isMaximized() else SC_MAXIMIZE
    _user32.PostMessageW(_hwnd(window), WM_SYSCOMMAND, command, 0)


def handle_native_message(window, message, is_caption_at, show_caption_menu):
    """Traite les messages de Windows qui décident du cadre.

    `message` : pointeur reçu par `nativeEvent` ; `is_caption_at(x, y)` :
    vrai si le point (coordonnées logiques de la fenêtre) est sur la partie
    de la barre de titre qui déplace la fenêtre ; `show_caption_menu(at_title_bar)` :
    ouvre le menu de la fenêtre, sous le curseur (clic droit sur cette
    partie) ou sous la barre (Alt+Espace).
    Retourne le couple (traité, résultat) attendu par `nativeEvent`, ou None
    pour laisser Qt traiter le message."""
    msg = wintypes.MSG.from_address(int(message))
    if msg.message == WM_NCCALCSIZE:
        return True, _client_rect(msg)
    if msg.message == WM_NCHITTEST:
        return _hit_test(window, msg, is_caption_at)
    if msg.message == WM_NCRBUTTONUP and msg.wParam == HTCAPTION:
        # Windows ouvrirait son menu système, non traduit : celui de
        # l'application le remplace.
        show_caption_menu(False)
        return True, 0
    if msg.message in (WM_SYSKEYDOWN, WM_SYSCHAR) and msg.wParam == VK_SPACE:
        # Alt+Espace : Qt ouvrirait lui-même le menu système de Windows dès
        # la touche enfoncée (jamais transmise à un QShortcut), et Windows le
        # caractère qui suit. Les deux sont retenus ; le menu de
        # l'application s'ouvre une fois, à la touche enfoncée.
        if msg.message == WM_SYSKEYDOWN:
            show_caption_menu(True)
        return True, 0
    return None


def _is_maximized(hwnd) -> bool:
    placement = WINDOWPLACEMENT()
    placement.length = ctypes.sizeof(WINDOWPLACEMENT)
    if not _user32.GetWindowPlacement(hwnd, ctypes.byref(placement)):
        return False
    return placement.showCmd == SW_SHOWMAXIMIZED


def _monitor_info(hwnd):
    monitor = _user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    if not monitor:
        return None
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if not _user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return None
    return info


def _auto_hide_edge(monitor_rect):
    """Bord de l'écran où se cache une barre des tâches masquée
    automatiquement, ou None s'il n'y en a pas sur cet écran."""
    data = APPBARDATA()
    data.cbSize = ctypes.sizeof(APPBARDATA)
    if not (_shell32.SHAppBarMessage(ABM_GETSTATE, ctypes.byref(data)) & ABS_AUTOHIDE):
        return None
    for edge in (ABE_BOTTOM, ABE_TOP, ABE_LEFT, ABE_RIGHT):
        data.uEdge = edge
        data.rc = monitor_rect
        if _shell32.SHAppBarMessage(ABM_GETAUTOHIDEBAREX, ctypes.byref(data)):
            return edge
    return None


def _same_rect(a, b) -> bool:
    return (a.left, a.top, a.right, a.bottom) == (b.left, b.top, b.right, b.bottom)


def _client_rect(msg) -> int:
    """WM_NCCALCSIZE : la zone cliente occupe toute la fenêtre proposée, sauf
    pour une fenêtre agrandie, ramenée à la zone de travail de son écran."""
    if msg.wParam:
        rect = ctypes.cast(msg.lParam, ctypes.POINTER(NCCALCSIZE_PARAMS)).contents.rgrc[0]
    else:
        rect = ctypes.cast(msg.lParam, ctypes.POINTER(wintypes.RECT)).contents
    if _is_maximized(msg.hWnd):
        info = _monitor_info(msg.hWnd)
        # Plein écran (F11) depuis une fenêtre agrandie : la fenêtre couvre
        # tout l'écran, barre des tâches comprise, rien à retirer.
        if info is not None and not _same_rect(rect, info.rcMonitor):
            work = info.rcWork
            rect.left = max(rect.left, work.left)
            rect.top = max(rect.top, work.top)
            rect.right = min(rect.right, work.right)
            rect.bottom = min(rect.bottom, work.bottom)
            edge = _auto_hide_edge(info.rcMonitor)
            if edge == ABE_BOTTOM:
                rect.bottom -= AUTO_HIDE_GAP
            elif edge == ABE_TOP:
                rect.top += AUTO_HIDE_GAP
            elif edge == ABE_LEFT:
                rect.left += AUTO_HIDE_GAP
            elif edge == ABE_RIGHT:
                rect.right -= AUTO_HIDE_GAP
    return WVR_REDRAW if msg.wParam else 0


def hit_test_code(x, y, width, height, border, horizontal=True, vertical=True):
    """Code de Windows pour un point (pixels physiques de la zone cliente)
    situé sur un bord qui redimensionne, ou None ailleurs. `horizontal` /
    `vertical` : la largeur / la hauteur de la fenêtre peut changer ; sans
    elle, les bords gauche et droit / haut et bas ne redimensionnent pas, et
    un coin ne garde que l'axe qui reste libre."""
    left = horizontal and x < border
    right = horizontal and x >= width - border
    top = vertical and y < border
    bottom = vertical and y >= height - border
    if top and left:
        return HTTOPLEFT
    if top and right:
        return HTTOPRIGHT
    if bottom and left:
        return HTBOTTOMLEFT
    if bottom and right:
        return HTBOTTOMRIGHT
    if top:
        return HTTOP
    if bottom:
        return HTBOTTOM
    if left:
        return HTLEFT
    if right:
        return HTRIGHT
    return None


def _hit_test(window, msg, is_caption_at):
    """WM_NCHITTEST : bords qui redimensionnent, puis barre de titre."""
    # Coordonnées d'écran signées sur 16 bits (écran à gauche du principal)
    point = wintypes.POINT(ctypes.c_short(msg.lParam & 0xFFFF).value,
                           ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value)
    _user32.ScreenToClient(msg.hWnd, ctypes.byref(point))
    client = wintypes.RECT()
    _user32.GetClientRect(msg.hWnd, ctypes.byref(client))
    if not (window.isMaximized() or window.isFullScreen()):
        # Dimension fixe (setFixedWidth, setFixedSize…) : lue à chaque fois,
        # une fenêtre pouvant changer de taille fixe pendant son affichage
        code = hit_test_code(point.x, point.y, client.right, client.bottom, RESIZE_BORDER,
                             window.minimumWidth() != window.maximumWidth(),
                             window.minimumHeight() != window.maximumHeight())
        if code is not None:
            return True, code
    ratio = window.devicePixelRatioF() or 1.0
    if is_caption_at(int(point.x / ratio), int(point.y / ratio)):
        return True, HTCAPTION
    return None
