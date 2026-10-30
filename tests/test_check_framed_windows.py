"""Code commun des fenêtres et barre de titre dessinée par Qt (skill window-title).

Scan AST de modules/qt/ et de MosaicView.py, sans Qt :
- toute fenêtre de premier niveau (sous-classe de QDialog ou de QMainWindow,
  directement ou par une classe du projet, ou widget qui se déclare fenêtre par
  setWindowFlags(Qt.Window ou Qt.Dialog)) hérite d'AppWindowMixin
  (app_window_qt), sauf les exceptions de EXCEPTIONS, avec leur raison : sans
  lui, la fenêtre perd ce qu'il câble une fois pour toutes (non-modalité,
  destruction à la fermeture, langue, thème et police, placement sur le
  panneau, Échap) et sa barre de titre serait celle de Windows, qui n'affiche
  ni le pIqaD ni le tengwar, ni le thème, ni la police de l'application ;
- FramedWindowMixin (la barre seule) n'est employé directement que par
  AppWindowMixin ;
- FramedWindowMixin, ou AppWindowMixin et QuestionWindowMixin (app_window_qt,
  bâtis sur lui), est placé avant la classe Qt dans les bases : il redéfinit
  setWindowFlags, setVisible, nativeEvent et les tailles (resize,
  setFixedSize…), qui doivent passer avant ceux de Qt ;
- une classe qui en hérite directement appelle dans son __init__
  self._setup_title_bar() (FramedWindowMixin) ou self._setup_window()
  (AppWindowMixin, QuestionWindowMixin : _setup_title_bar est appelé par
  AppWindowMixin._setup_window), et pose son titre
  par set_window_title (clé de traduction) ou set_window_title_text (nom
  propre non traduit) ;
- setWindowTitle n'est appelé que par la barre elle-même et par le titre de
  la fenêtre principale : posé ailleurs, le titre ne serait ni retraduit ni
  affiché dans la barre dessinée par Qt.
"""
import ast
from pathlib import Path

import ast_loader

ROOT = Path(__file__).resolve().parent.parent
QT_DIR = ROOT / "modules" / "qt"
FILES = sorted(QT_DIR.glob("*.py")) + [ROOT / "MosaicView.py"]

QT_WINDOW_BASES = {"QDialog", "QMainWindow"}

# Mixin de fenêtre → appel de mise en place attendu dans __init__
WINDOW_MIXINS = {
    "FramedWindowMixin": "self._setup_title_bar()",
    "AppWindowMixin": "self._setup_window(",
    "QuestionWindowMixin": "self._setup_window(",
}

# Fenêtres de premier niveau sans AppWindowMixin, avec leur raison
EXCEPTIONS = {
    # barre de titre propre, un titre par panneau (title_bar_qt.TitleBar)
    ("MosaicView.py", "MainWindow"),
}

# Seuls endroits qui écrivent le titre lu par Windows (barre des tâches, Alt+Tab)
SET_WINDOW_TITLE_ALLOWED = {
    # barre dessinée par Qt : _wt() pour Windows, _() pour la barre
    "framed_window_qt.py",
    # fenêtre principale : nom des fichiers ouverts dans les panneaux
    "window_title_qt.py",
    # fenêtre principale : titre posé avant la création des panneaux
    "MosaicView.py",
}


def _base_names(node):
    return [ast.unparse(b).split(".")[-1] for b in node.bases]


def _classes(files=FILES, trees=None):
    """{nom: [(fichier, nœud)…]} de toutes les classes, tous fichiers
    confondus (une fenêtre peut hériter d'une classe d'un autre module ; deux
    modules peuvent avoir une classe du même nom)."""
    out = {}
    for path in files:
        tree = trees[path] if trees else ast_loader.module_tree(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                out.setdefault(node.name, []).append((Path(path).name, node))
    return out


def _all(classes):
    return [(fname, node) for found in classes.values() for fname, node in found]


def _inherits(classes, name, target, seen=()):
    if name == target:
        return True
    if name not in classes or name in seen:
        return False
    return any(_inherits(classes, b, target, seen + (name,))
               for _f, node in classes[name] for b in _base_names(node))


def _declares_window(node):
    for call in ast.walk(node):
        if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and call.func.attr == "setWindowFlags"):
            flags = ast.unparse(call.args[0]) if call.args else ""
            if "Qt.Window" in flags.replace("Qt.WindowType.", "Qt.") or "Qt.Dialog" in flags:
                return True
    return False


def _node_inherits(classes, node, target):
    return any(_inherits(classes, b, target, (node.name,)) for b in _base_names(node))


def _windows(classes):
    for fname, node in _all(classes):
        if node.name == "FramedWindowMixin":
            continue
        if (any(_node_inherits(classes, node, base) for base in QT_WINDOW_BASES)
                or _declares_window(node)):
            yield fname, node.name, node


def _unframed(classes):
    return [(f, name) for f, name, node in _windows(classes)
            if not _node_inherits(classes, node, "AppWindowMixin")
            and (f, name) not in EXCEPTIONS]


def _direct_framed(classes):
    """Classes qui prennent la barre seule, sans le code commun."""
    return [f"{f}:{node.name}" for f, node in _all(classes)
            if "FramedWindowMixin" in _base_names(node) and node.name != "AppWindowMixin"]


def _misplaced_mixin(classes):
    wrong = []
    for fname, node in _all(classes):
        bases = _base_names(node)
        qt = [i for i, b in enumerate(bases) if b.startswith("Q")]
        for mixin in WINDOW_MIXINS:
            if mixin in bases and qt and bases.index(mixin) > min(qt):
                wrong.append(f"{fname}:{node.name}")
    return wrong


def _missing_setup(classes):
    wrong = []
    for fname, node in _all(classes):
        if node.name in WINDOW_MIXINS:
            continue  # les mixins eux-mêmes : test_app_window_mixin_sets_up_the_title_bar
        for mixin, setup in WINDOW_MIXINS.items():
            if mixin not in _base_names(node):
                continue
            init = next((m for m in node.body
                         if isinstance(m, ast.FunctionDef) and m.name == "__init__"), None)
            if init is None or setup not in ast.unparse(init):
                wrong.append(f"{fname}:{node.name} ({setup.split('.')[1].rstrip('()')})")
            source = ast.unparse(node)
            if "set_window_title(" not in source and "set_window_title_text(" not in source:
                wrong.append(f"{fname}:{node.name} (set_window_title)")
    return wrong


def test_scan_finds_the_windows():
    names = {name for _f, name, _n in _windows(_classes())}
    assert len(names) >= 80
    assert {"ImageViewer", "LibraryWindow", "MsgDialog", "ErrorDialog",
            "MainWindow", "_BookmarkPopup"} <= names


def test_every_window_has_the_qt_title_bar():
    wrong = _unframed(_classes())
    assert not wrong, ("hériter d'AppWindowMixin (app_window_qt), ou "
                       "ajouter la fenêtre à EXCEPTIONS avec sa raison :\n"
                       + "\n".join(f"{f}:{n}" for f, n in wrong))


def test_title_bar_alone_only_in_the_common_code():
    wrong = _direct_framed(_classes())
    assert not wrong, ("hériter d'AppWindowMixin, qui pose la barre de titre "
                       "avec le reste du code commun :\n" + "\n".join(wrong))


def test_exceptions_are_still_needed():
    classes = _classes()
    windows = {(f, name): node for f, name, node in _windows(classes)}
    for key in EXCEPTIONS:
        assert key in windows, key
        assert not _node_inherits(classes, windows[key], "AppWindowMixin"), key


def test_mixin_comes_before_the_qt_class():
    wrong = _misplaced_mixin(_classes())
    assert not wrong, wrong


def test_framed_windows_set_up_their_bar_and_title():
    wrong = _missing_setup(_classes())
    assert not wrong, wrong


def test_set_window_title_only_in_the_title_bars():
    wrong = []
    for path in FILES:
        if path.name in SET_WINDOW_TITLE_ALLOWED:
            continue
        for node in ast.walk(ast_loader.module_tree(path)):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "setWindowTitle"):
                wrong.append(f"{path.name}:{node.lineno}")
    assert not wrong, ("titre par set_window_title(clé) de FramedWindowMixin :\n"
                       + "\n".join(wrong))


def test_app_window_mixin_sets_up_the_title_bar():
    """Une fenêtre à AppWindowMixin n'appelle que _setup_window : c'est lui
    qui doit poser la barre de titre."""
    found = _classes()["AppWindowMixin"]
    assert len(found) == 1 and found[0][0] == "app_window_qt.py"
    node = found[0][1]
    assert _base_names(node) == ["FramedWindowMixin"]
    setup = next(m for m in node.body
                 if isinstance(m, ast.FunctionDef) and m.name == "_setup_window")
    assert "self._setup_title_bar()" in ast.unparse(setup)


def test_scan_detects_each_fault():
    source = (
        "class Base(FramedWindowMixin, QDialog):\n"
        "    def __init__(self, p):\n"
        "        super().__init__(p)\n"
        "        self._setup_title_bar()\n"
        "        self.set_window_title('k')\n"
        "class Child(Base):\n"
        "    pass\n"
        "class Plain(QDialog):\n"
        "    pass\n"
        "class Grand(Plain):\n"
        "    pass\n"
        "class Floating(QWidget):\n"
        "    def __init__(self):\n"
        "        self.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint)\n"
        "class Panel(QWidget):\n"
        "    def __init__(self):\n"
        "        self.setWindowFlags(Qt.FramelessWindowHint)\n"
        "class Reversed(QDialog, FramedWindowMixin):\n"
        "    def __init__(self, p):\n"
        "        self.set_window_title('k')\n"
        "class AppWindowMixin(FramedWindowMixin):\n"
        "    pass\n"
        "class Common(AppWindowMixin, QDialog):\n"
        "    def __init__(self, p):\n"
        "        self._setup_window()\n"
        "        self.set_window_title('k')\n"
        "class OnlyBar(AppWindowMixin, QDialog):\n"
        "    def __init__(self, p):\n"
        "        self._setup_title_bar()\n"
        "        self.set_window_title('k')\n"
        "class AppReversed(QDialog, AppWindowMixin):\n"
        "    def __init__(self, p):\n"
        "        self._setup_window()\n")
    path = Path("synthetic.py")
    classes = _classes([path], {path: ast.parse(source)})
    assert sorted(_unframed(classes)) == [
        ("synthetic.py", "Base"), ("synthetic.py", "Child"), ("synthetic.py", "Floating"),
        ("synthetic.py", "Grand"), ("synthetic.py", "Plain"), ("synthetic.py", "Reversed")]
    assert _direct_framed(classes) == ["synthetic.py:Base", "synthetic.py:Reversed"]
    assert _misplaced_mixin(classes) == ["synthetic.py:Reversed", "synthetic.py:AppReversed"]
    assert _missing_setup(classes) == ["synthetic.py:Reversed (_setup_title_bar)",
                                       "synthetic.py:OnlyBar (_setup_window)",
                                       "synthetic.py:AppReversed (set_window_title)"]
