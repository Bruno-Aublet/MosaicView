"""Outil de la visionneuse déjà sélectionné pendant une opération en arrière-plan.

Une opération revient au sommet de l'historique si elle est annulée. Un outil
qui écrirait pendant ce temps y ajouterait des états contenant les pages à moitié
traitées, et Annuler ne rétablirait plus le document. Vérifié :
- une commande d'écriture est refusée, et les réglages sont remis à jour ;
- elle s'exécute normalement sans opération, et pendant une lecture de macro
  (skip_history=True), elle-même une opération ;
- tout perform_* d'un outil est couvert, et la protection est posée sur ImageViewer ;
- aucun geste d'outil ne démarre sur la page, le déplacement au clic droit reste
  possible.
"""
import ast
import types

from modules.qt.viewer_busy import (DOCUMENT_WRITING_COMMANDS, guard_document_commands,
                                    refused_during_operation)

import ast_loader

LEFT, RIGHT = 1, 2
FakeQt = types.SimpleNamespace(LeftButton=LEFT, RightButton=RIGHT, MouseFocusReason="souris")


class Tool:
    def __init__(self, busy):
        self.busy = busy
        self.calls = []
        self.resynced = 0

    def _document_busy(self):
        return self.busy

    def _after_refused_commit(self):
        self.resynced += 1

    @refused_during_operation
    def perform_effect(self, key, skip_history=False):
        self.calls.append((key, skip_history))
        return True


def test_command_is_refused_during_an_operation():
    tool = Tool(busy=True)
    assert tool.perform_effect("sepia") is False
    assert tool.calls == [] and tool.resynced == 1


def test_command_runs_without_an_operation():
    tool = Tool(busy=False)
    assert tool.perform_effect("sepia") is True
    assert tool.calls == [("sepia", False)] and tool.resynced == 0


def test_macro_reading_is_not_refused():
    tool = Tool(busy=True)
    assert tool.perform_effect("sepia", skip_history=True) is True
    assert tool.calls == [("sepia", True)] and tool.resynced == 0


def test_every_tool_command_is_covered():
    found = set()
    for path in ast_loader.QT_DIR.glob("*_tool_qt.py"):
        for cls in ast_loader.module_tree(path).body:
            if isinstance(cls, ast.ClassDef) and cls.name.endswith("ViewerMixin"):
                found |= {f.name for f in cls.body if isinstance(f, ast.FunctionDef)
                          and f.name.startswith("perform_") and not f.name.endswith("_step")}
    assert found == set(DOCUMENT_WRITING_COMMANDS)


def test_guard_replaces_every_command_of_a_class():
    def command(self, skip_history=False):
        return "écrit"

    viewer_class = type("V", (), {name: command for name in DOCUMENT_WRITING_COMMANDS})
    viewer_class._document_busy = lambda self: True
    viewer_class._after_refused_commit = lambda self: None
    guard_document_commands(viewer_class)
    viewer = viewer_class()
    assert all(getattr(viewer, name)() is False for name in DOCUMENT_WRITING_COMMANDS)


def test_image_viewer_is_guarded():
    tree = ast_loader.module_tree(ast_loader.QT_DIR / "image_viewer_qt.py")
    calls = [ast.unparse(n.value) for n in tree.body if isinstance(n, ast.Expr)]
    assert "guard_document_commands(ImageViewer)" in calls


CanvasBase = ast_loader.load_methods("image_viewer_qt.py", "_ViewerCanvas",
                                     {"mousePressEvent"}, Qt=FakeQt)


class Canvas(CanvasBase):
    def __init__(self, busy):
        self._viewer = types.SimpleNamespace(
            _document_busy=lambda: busy,
            _toolbar=types.SimpleNamespace(active_tool="blur"))
        self._ignore_crop_events = False
        self._pan_start = None
        self._is_panning = False
        self.painted = 0

    def setFocus(self, reason):
        pass

    def blur_mouse_press(self, event):
        self.painted += 1


def press(button):
    return types.SimpleNamespace(
        button=lambda: button,
        position=lambda: types.SimpleNamespace(toPoint=lambda: "point"))


def test_tool_gesture_does_not_start_during_an_operation():
    canvas = Canvas(busy=True)
    canvas.mousePressEvent(press(LEFT))
    assert canvas.painted == 0


def test_tool_gesture_starts_without_an_operation():
    canvas = Canvas(busy=False)
    canvas.mousePressEvent(press(LEFT))
    assert canvas.painted == 1


def test_right_button_pan_stays_available_during_an_operation():
    canvas = Canvas(busy=True)
    canvas.mousePressEvent(press(RIGHT))
    assert canvas._pan_start == "point"


ResyncBase = ast_loader.load_methods("image_viewer_qt.py", "ImageViewer", {"_after_refused_commit"})


def test_refused_command_puts_the_tool_settings_back():
    calls = []

    class Viewer(ResyncBase):
        def __getattr__(self, name):
            return lambda *args, **kwargs: calls.append(name)

    Viewer()._after_refused_commit()
    assert {"_reset_sharpness_preview", "_reset_brightness_preview", "_reset_saturation_preview",
            "_reset_remove_colors_preview", "_reset_compression_preview", "_reset_levels_preview",
            "_sync_color_depth_panel", "_sync_effects_panel", "_sync_image_mode_panel",
            "display_image"} <= set(calls)
