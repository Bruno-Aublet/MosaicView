"""Coup de pinceau en cours dans la visionneuse (clonage, tampon de flou).

L'image de travail d'un trait est écrite au relâchement du clic, sur la page
alors affichée. Vérifié :
- clic maintenu : page, mode de lecture, Annuler, Refaire et Ctrl+V sans effet,
  le trait va sur sa page et la page voisine reste intacte ;
- pas de menu contextuel pendant un trait (il capterait le relâchement) ;
- Échap n'annule que l'aperçu de ligne droite et enregistre ce qui est peint,
  sans jamais fermer la fenêtre ni laisser une image de travail pour la suite ;
- relâchement jamais reçu ou fenêtre quittée : le trait est enregistré sur sa
  page, et la commande suivante n'est pas bloquée ;
- enregistrement impossible : l'image de travail est abandonnée, le trait
  suivant repart de la page affichée.

Les modules de la visionneuse importent PySide6 : les méthodes sont extraites
par AST (tests/ast_loader.load_methods), l'interface est remplacée.
"""
import io
import types

from PIL import Image

import ast_loader

LEFT, RIGHT, NO_BUTTON = 1, 2, 0
ACTIVATION_CHANGE = "activation"
FakeQt = types.SimpleNamespace(LeftButton=LEFT, RightButton=RIGHT, ArrowCursor="flèche")
FakeQEvent = types.SimpleNamespace(ActivationChange=ACTIVATION_CHANGE)
mouse = types.SimpleNamespace(buttons=NO_BUTTON)
FakeApplication = types.SimpleNamespace(mouseButtons=lambda: mouse.buttons)


class Writes:
    fail = False


def write_entry(entry):
    if Writes.fail:
        raise OSError("disque plein")
    buf = io.BytesIO()
    entry["img"].convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


class FakeDialog:
    shown = 0
    keys = []


def fake_failure(parent, title_key, message_key, entry=None):
    FakeDialog.shown += 1
    FakeDialog.keys.append(message_key)


def forget_image(images_data, index):
    images_data[index]["img"] = None


TOOL_DEPS = dict(
    io=io, Image=Image, _state_module=None, page_operation_failed=fake_failure,
    forget_unsaved_page_image=forget_image,
    save_image_to_bytes=write_entry,
    drawing_base_image=lambda data: Image.open(io.BytesIO(data)).convert("RGBA"),
    drawn_page_image=lambda data, img: img,
)

ViewerBase = ast_loader.load_methods(
    "image_viewer_qt.py", "ImageViewer",
    {"navigate", "_show_image_position", "toggle_double_page", "_can_change_reading_mode", "_document_busy",
     "_undo_and_refresh", "_redo_and_refresh",
     "_block_undo_redo_for_unvalidated_work", "_paint_stroke_active", "_finish_paint_strokes",
     "_paint_stroke_blocks_command", "changeEvent", "_on_escape", "_paste_image_shortcut"},
    QApplication=FakeApplication, Qt=FakeQt, QEvent=FakeQEvent, _state_module=None,
)
BlurBase = ast_loader.load_methods(
    "blur_tool_qt.py", "BlurViewerMixin",
    {"_on_blur_paint_stroke", "_on_blur_paint_end", "_blur_stroke_active", "_drop_blur_stroke",
     "_finish_blur_stroke", "_cancel_blur_line_preview"},
    **TOOL_DEPS,
)
CloneBase = ast_loader.load_methods(
    "clone_tool_qt.py", "CloneViewerMixin",
    {"_on_clone_paint_end", "_clone_stroke_active", "_drop_clone_stroke", "_finish_clone_stroke",
     "_cancel_clone_line_preview"},
    **TOOL_DEPS,
)
ReleaseEvent = ast_loader.load_methods("image_viewer_qt.py", "_ViewerCanvas",
                                       {"mouseReleaseEvent"}, Qt=FakeQt)


class QtParent:
    def changeEvent(self, event):
        pass


class Canvas(ReleaseEvent):
    def __init__(self, viewer):
        self._viewer = viewer
        self._blur_painting = False
        self._blur_paint_last = None
        self._blur_line_mode = False
        self._blur_line_end = None
        self._blur_brush_radius = 10
        self._blur_strength = 5
        self._clone_painting = False
        self._clone_paint_last = None
        self._clone_line_mode = False
        self._clone_line_end = None
        self._clone_live_marker_widget = None
        self._clone_mode = "fixed"
        self._clone_source_img = None
        self._clone_stroke_start_dest = (0, 0)
        self._clone_stroke_start_src = (5, 5)
        self._clone_stroke_last_dest = (1, 1)
        self.has_crop = False
        self._crop_start = None
        self.has_line = False
        self._line_start = None
        self.has_text_blocks = False
        self._shape_draw_start = None
        self.has_shapes = False
        self.has_pasted_images = False
        self._is_panning = False
        self._pan_start = None

    def update(self):
        pass

    def clear_clone_source(self):
        self._clone_source_img = None

    def setCursor(self, cursor):
        pass


class Viewer(ViewerBase, BlurBase, CloneBase, QtParent):
    """Deux pages rouge et bleue, le flou réel écrit vert sur la page affichée."""

    def __init__(self):
        self.busy = False
        self.state = types.SimpleNamespace(images_data=[page("red"), page("blue")], modified=False,
                                           comic_metadata=None, operation_running=lambda: self.busy)
        self.saved_states = []
        self.undone = []
        self.callbacks = {
            "state": self.state,
            "save_state": lambda force=False: self.saved_states.append(force),
            "undo_action": lambda: self.undone.append("annuler"),
            "redo_action": lambda: self.undone.append("refaire"),
        }
        self.current_idx = 0
        self.page_mode = "single"
        self.zoom_level = 1.0
        self.is_fullscreen = False
        self._macro_recording = False
        self._macro_reading = False
        self._canvas = Canvas(self)
        self._blur_work_img = None
        self._blur_stroke_dirty = False
        self._blur_display_timer = types.SimpleNamespace(elapsed=lambda: 0, restart=lambda: None)
        self._clone_work_img = None
        self._clone_checker_bg = None
        self._clone_stroke_snapshot = None
        self._clone_bytes_before_stroke = None
        self._clone_stroke_dirty = False
        self._center_parent = None
        self._crop_by_page = {}
        self._straighten_by_page = {}
        self._text_blocks_by_page = {}
        self._shapes_by_page = {}
        self._pasted_images_by_page = {}
        self._transp_work_img_by_page = {}
        self._sharpness_preview_img = None
        self.closed = False
        self.pasted = 0
        self.modes = []
        self.active_window = True
        self.menus = 0
        panel = types.SimpleNamespace(active_pipette=None)
        self._toolbar = types.SimpleNamespace(
            active_tool="blur", refresh_undo_redo_state=lambda: None,
            _levels_panel=panel, _transparency_panel=panel,
            paste_image_from_clipboard=self._count_paste,
        )

    def __getattr__(self, name):
        # Rafraîchissements de l'interface appelés par navigate et le commit.
        if name.startswith(("_save_", "_restore_", "_reset_", "_refresh_", "_sync_", "_update_",
                            "_check_clear_", "_macro_record_")):
            return lambda *args, **kwargs: None
        raise AttributeError(name)

    def _count_paste(self):
        self.pasted += 1

    def get_image_indices(self):
        return [0, 1]

    def display_image(self, keep_crop_rect=False):
        pass

    def _blur_refresh_display(self):
        pass

    def _clone_refresh_display(self):
        pass

    def _blur_apply_stamp(self, x, y):
        self._blur_work_img.paste((0, 255, 0, 255), (0, 0, 4, 4))

    def _has_unvalidated_work(self):
        return False

    def set_page_mode(self, mode, keep_zoom=False):
        self.modes.append(mode)
        self.page_mode = mode

    def close(self):
        self.closed = True

    def isActiveWindow(self):
        return self.active_window

    def _show_context_menu(self, pos):
        self.menus += 1

    def start_blur_stroke(self):
        """Clic gauche maintenu après un premier point peint."""
        mouse.buttons = LEFT
        self._canvas._blur_painting = True
        self._on_blur_paint_stroke(1, 1)

    def release_blur_stroke(self):
        mouse.buttons = NO_BUTTON
        self._canvas._blur_painting = False
        self._on_blur_paint_end()


CloneStrokeBase = ast_loader.load_methods(
    "clone_tool_qt.py", "CloneViewerMixin", {"_on_clone_paint_stroke"},
    make_clone_checker=lambda width, height: None, **TOOL_DEPS,
)


class CloneRecorder(CloneStrokeBase):
    """Capture macro du clonage, sans le tamponnage lui-même."""

    def __init__(self):
        state = types.SimpleNamespace(images_data=[page("red")])
        self.callbacks = {"state": state}
        self.current_idx = 0
        self.page_mode = "single"
        self._macro_recording = True
        self._clone_stroke_dirty = False
        self._clone_display_timer = types.SimpleNamespace(elapsed=lambda: 0, restart=lambda: None)
        self._canvas = types.SimpleNamespace(
            _clone_source_img=(5, 5), _clone_mode="fixed", _clone_stroke_start_dest=None,
            _clone_stroke_start_src=None, _clone_stroke_last_dest=None,
            _get_effective_clone_source=lambda x, y: (x, y))

    def _clone_apply_stamp(self, *args):
        pass

    def _clone_refresh_display(self):
        pass


def page(color):
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, format="PNG")
    return {"bytes": buf.getvalue(), "extension": ".png", "is_image": True, "orig_name": color}


def corner(entry):
    return Image.open(io.BytesIO(entry["bytes"])).convert("RGB").getpixel((0, 0))


def far_corner(entry):
    return Image.open(io.BytesIO(entry["bytes"])).convert("RGB").getpixel((7, 7))


GREEN, RED, BLUE = (0, 255, 0), (255, 0, 0), (0, 0, 255)


def setup_function(_function):
    mouse.buttons = NO_BUTTON
    Writes.fail = False
    FakeDialog.shown = 0
    FakeDialog.keys = []


def test_no_page_change_during_a_stroke_so_the_next_page_stays_intact():
    viewer = Viewer()
    viewer.start_blur_stroke()
    viewer.navigate(1)
    assert viewer.current_idx == 0
    viewer.release_blur_stroke()
    data = viewer.state.images_data
    assert corner(data[0]) == GREEN and far_corner(data[0]) == RED
    assert corner(data[1]) == BLUE


def test_page_change_is_refused_during_a_clone_stroke_too():
    viewer = Viewer()
    mouse.buttons = LEFT
    viewer._canvas._clone_painting = True
    viewer.navigate(1)
    assert viewer.current_idx == 0


def test_page_changes_again_once_the_stroke_is_finished():
    viewer = Viewer()
    viewer.start_blur_stroke()
    viewer.release_blur_stroke()
    viewer.navigate(1)
    assert viewer.current_idx == 1


def test_reading_mode_undo_redo_and_paste_do_nothing_during_a_stroke():
    viewer = Viewer()
    viewer.start_blur_stroke()
    viewer._toolbar.active_tool = None   # le trait suffit à bloquer, même outil retiré
    viewer.toggle_double_page()
    viewer._undo_and_refresh()
    viewer._redo_and_refresh()
    viewer._paste_image_shortcut()
    assert viewer.modes == [] and viewer.undone == [] and viewer.pasted == 0


def test_no_context_menu_during_a_stroke():
    viewer = Viewer()
    release = types.SimpleNamespace(
        button=lambda: RIGHT,
        globalPosition=lambda: types.SimpleNamespace(toPoint=lambda: None))
    viewer.start_blur_stroke()
    viewer._canvas.mouseReleaseEvent(release)
    assert viewer.menus == 0
    viewer.release_blur_stroke()
    viewer._canvas.mouseReleaseEvent(release)
    assert viewer.menus == 1


def test_escape_during_a_free_stroke_changes_nothing():
    viewer = Viewer()
    viewer._canvas.has_text_blocks = True
    viewer.start_blur_stroke()
    viewer._on_escape()
    assert viewer.closed is False
    assert viewer._blur_stroke_dirty is True and viewer._canvas._blur_painting is True
    assert viewer._canvas.has_text_blocks is True


def test_escape_keeps_the_clone_source_during_a_stroke():
    viewer = Viewer()
    mouse.buttons = LEFT
    viewer._canvas._clone_painting = True
    viewer._canvas._clone_source_img = (2, 2)
    viewer._on_escape()
    assert viewer._canvas._clone_source_img == (2, 2)


def test_escape_on_a_line_preview_saves_the_painted_part_and_leaves_nothing_for_later():
    viewer = Viewer()
    viewer.start_blur_stroke()
    viewer._canvas._blur_line_mode = True
    viewer._canvas._blur_line_end = (7, 7)
    viewer._on_escape()
    data = viewer.state.images_data
    assert corner(data[0]) == GREEN and far_corner(data[0]) == RED
    assert viewer._blur_stroke_dirty is False and viewer._blur_work_img is None
    assert viewer._canvas._blur_line_mode is False
    # La suite du trait sur une autre page ne reprend pas l'image de la première.
    mouse.buttons = NO_BUTTON
    viewer.navigate(1)
    viewer.start_blur_stroke()
    viewer.release_blur_stroke()
    assert far_corner(data[1]) == BLUE


def test_escape_on_a_line_preview_without_a_painted_part_writes_nothing():
    viewer = Viewer()
    before = viewer.state.images_data[0]["bytes"]
    mouse.buttons = LEFT
    viewer._canvas._blur_painting = True
    viewer._canvas._blur_line_mode = True
    viewer._on_escape()
    assert viewer.state.images_data[0]["bytes"] == before
    assert viewer.saved_states == [] and viewer._canvas._blur_painting is False


def test_escape_still_closes_the_viewer_without_a_stroke():
    viewer = Viewer()
    viewer._on_escape()
    assert viewer.closed is True


def test_lost_release_saves_the_stroke_on_its_page_then_lets_the_command_run():
    viewer = Viewer()
    viewer.start_blur_stroke()
    mouse.buttons = NO_BUTTON            # relâchement jamais reçu par le canvas
    viewer.navigate(1)
    data = viewer.state.images_data
    assert viewer.current_idx == 1
    assert corner(data[0]) == GREEN and corner(data[1]) == BLUE
    assert viewer._blur_stroke_dirty is False and viewer._canvas._blur_painting is False


def test_leaving_the_window_saves_the_stroke():
    viewer = Viewer()
    viewer.start_blur_stroke()
    viewer.active_window = False
    viewer.changeEvent(types.SimpleNamespace(type=lambda: ACTIVATION_CHANGE))
    assert corner(viewer.state.images_data[0]) == GREEN
    assert viewer._blur_stroke_dirty is False


def test_other_window_events_leave_the_stroke_alone():
    viewer = Viewer()
    viewer.start_blur_stroke()
    viewer.active_window = False
    viewer.changeEvent(types.SimpleNamespace(type=lambda: "autre"))
    assert viewer._blur_stroke_dirty is True


def test_macro_notes_the_first_blur_point_once():
    viewer = Viewer()
    viewer._macro_recording = True
    viewer._canvas._blur_painting = True
    mouse.buttons = LEFT
    viewer._on_blur_paint_stroke(1, 1)
    viewer._on_blur_paint_stroke(2, 2)
    assert viewer._macro_blur_points == [(1, 1), (2, 2)]


def test_macro_notes_the_first_clone_point_once():
    viewer = CloneRecorder()
    viewer._on_clone_paint_stroke(1, 1)
    viewer._on_clone_paint_stroke(2, 2)
    assert viewer._macro_clone_points == [(1, 1), (2, 2)]


def test_undo_and_redo_do_nothing_during_an_operation():
    viewer = Viewer()
    viewer.busy = True
    viewer._undo_and_refresh()
    viewer._redo_and_refresh()
    assert viewer.undone == []


def test_blur_stroke_is_dropped_if_an_operation_started_meanwhile():
    viewer = Viewer()
    before = viewer.state.images_data[0]["bytes"]
    viewer.start_blur_stroke()
    viewer.busy = True
    viewer.release_blur_stroke()
    assert viewer.state.images_data[0]["bytes"] == before
    assert viewer.saved_states == []
    assert viewer._blur_stroke_dirty is False and viewer._blur_work_img is None


def test_clone_stroke_is_dropped_if_an_operation_started_meanwhile():
    viewer = Viewer()
    before = viewer.state.images_data[0]["bytes"]
    viewer._clone_work_img = Image.new("RGBA", (8, 8), GREEN)
    viewer._clone_stroke_dirty = True
    viewer.busy = True
    assert viewer._on_clone_paint_end() is False
    assert viewer.state.images_data[0]["bytes"] == before
    assert viewer.saved_states == []
    assert viewer._clone_stroke_dirty is False and viewer._clone_work_img is None


def test_failed_save_drops_the_blur_work_image():
    viewer = Viewer()
    viewer.start_blur_stroke()
    Writes.fail = True
    viewer.release_blur_stroke()
    assert FakeDialog.keys == ["messages.errors.blur_failed.message"]
    assert viewer._blur_stroke_dirty is False and viewer._blur_work_img is None
    assert viewer.state.images_data[0]["img"] is None   # résultat non écrit, retiré de la page
    Writes.fail = False
    viewer.navigate(1)
    viewer.start_blur_stroke()
    viewer.release_blur_stroke()
    assert far_corner(viewer.state.images_data[1]) == BLUE   # jamais l'image de la page 1


def test_failed_save_drops_the_clone_work_image():
    viewer = Viewer()
    viewer._clone_work_img = Image.new("RGBA", (8, 8), GREEN)
    viewer._clone_stroke_snapshot = viewer._clone_work_img
    viewer._clone_stroke_dirty = True
    Writes.fail = True
    assert viewer._on_clone_paint_end() is False
    assert FakeDialog.keys == ["messages.errors.clone_failed.message"]
    assert viewer._clone_stroke_dirty is False
    assert viewer._clone_work_img is None and viewer._clone_stroke_snapshot is None
    assert viewer.state.images_data[0]["img"] is None
    assert viewer._canvas._clone_stroke_start_dest is None
