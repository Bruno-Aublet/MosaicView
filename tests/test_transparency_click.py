"""Outil Transparence : clic sur la page et aperçu d'un travail en attente.

Le clic se calcule sur toute l'image d'un coup (numpy) et doit donner exactement
le résultat du parcours pixel par pixel, écrit ici comme référence. L'aperçu
reste visible en revenant sur une page dont le travail n'est pas validé.

Les modules importent PySide6 : les méthodes sont extraites par AST
(tests/ast_loader.load_methods), l'interface est remplacée.
"""
import random
import types

import pytest
from PIL import Image

import ast_loader

zone_of = ast_loader.load("transparency_tool_qt.py", {"_connected_zone"})["_connected_zone"]
Base = ast_loader.load_methods(
    "transparency_tool_qt.py", "TransparencyViewerMixin", {"apply_transparency_click"},
    Image=Image, _connected_zone=zone_of,
)


class Viewer(Base):
    def __init__(self, img, kind="zone", tolerance=10):
        self.img = img
        self.previews = 0
        self._toolbar = types.SimpleNamespace(
            _transparency_panel=types.SimpleNamespace(transparency_type=kind, tolerance=tolerance))
        self._canvas = types.SimpleNamespace(_update_validate_btn_state=lambda: None,
                                             _update_cancel_btn_state=lambda: None)

    def _get_or_init_transp_work_img(self):
        return self.img

    def _update_transparency_preview(self):
        self.previews += 1


def reference(img, kind, tol, px, py):
    """Parcours pixel par pixel, résultat attendu du clic."""
    result = img.copy()
    pixels = result.load()
    width, height = result.size
    ref = pixels[px, py]

    def matches(c):
        return c[3] != 0 and all(abs(c[i] - ref[i]) <= tol for i in range(3))

    if kind == "global":
        for y in range(height):
            for x in range(width):
                if matches(pixels[x, y]):
                    pixels[x, y] = (*pixels[x, y][:3], 0)
        return result
    stack, seen = [(px, py)], set()
    while stack:
        x, y = stack.pop()
        if (x, y) in seen or not (0 <= x < width and 0 <= y < height):
            continue
        seen.add((x, y))
        if matches(pixels[x, y]):
            pixels[x, y] = (*pixels[x, y][:3], 0)
            stack += [(x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)]
    return result


def noisy_image(seed, levels):
    rng = random.Random(seed)
    img = Image.new("RGBA", (40, 30))
    img.putdata([(rng.choice(levels), rng.choice(levels), rng.choice(levels),
                  rng.choice((0, 128, 255, 255, 255))) for _i in range(40 * 30)])
    return img


@pytest.mark.parametrize("kind", ["zone", "global"])
@pytest.mark.parametrize("seed", range(6))
def test_click_gives_the_same_result_as_a_pixel_by_pixel_walk(kind, seed):
    img = noisy_image(seed, (0, 10, 40, 200))
    px, py = next((x, y) for y in range(30) for x in range(40) if img.getpixel((x, y))[3])
    expected = reference(img, kind, 15, px, py)
    viewer = Viewer(img, kind, 15)
    assert viewer.apply_transparency_click(px, py) is True
    assert img.tobytes() == expected.tobytes()
    assert viewer.previews == 1


def test_zone_does_not_cross_a_diagonal():
    img = Image.new("RGBA", (3, 3), (0, 0, 0, 255))
    for point in ((0, 0), (1, 1), (2, 2)):
        img.putpixel(point, (255, 255, 255, 255))
    Viewer(img, "zone", 0).apply_transparency_click(0, 0)
    assert img.getpixel((0, 0))[3] == 0
    assert img.getpixel((1, 1))[3] == 255


def test_zone_goes_around_an_obstacle():
    img = Image.new("RGBA", (5, 5), (255, 255, 255, 255))
    for y in range(4):
        img.putpixel((2, y), (0, 0, 0, 255))
    Viewer(img, "zone", 0).apply_transparency_click(0, 0)
    assert img.getpixel((4, 0))[3] == 0
    assert img.getpixel((2, 0))[3] == 255


def test_colors_stay_under_the_transparency():
    img = Image.new("RGBA", (4, 4), (12, 34, 56, 255))
    Viewer(img, "global", 0).apply_transparency_click(1, 1)
    assert img.getpixel((2, 2)) == (12, 34, 56, 0)


def test_click_on_a_transparent_pixel_or_outside_does_nothing():
    img = Image.new("RGBA", (4, 4), (1, 2, 3, 0))
    viewer = Viewer(img)
    assert viewer.apply_transparency_click(1, 1) is False
    assert viewer.apply_transparency_click(9, 9) is False
    assert viewer.apply_transparency_click(-1, 0) is False
    assert viewer.previews == 0


def test_global_macro_step_uses_its_recorded_color():
    img = Image.new("RGBA", (2, 1), (9, 9, 9, 255))
    img.putpixel((1, 0), (200, 200, 200, 255))
    Viewer(img).apply_transparency_click(None, None, override_type="global",
                                         override_tolerance=0, override_color=(200, 200, 200))
    assert img.getpixel((0, 0))[3] == 255 and img.getpixel((1, 0))[3] == 0


NavigateBase = ast_loader.load_methods("image_viewer_qt.py", "ImageViewer",
                                       {"navigate", "_show_image_position"})


class Navigator(NavigateBase):
    """Rend la liste des étapes de navigate, dans leur ordre."""

    def __init__(self, current):
        self.current_idx = current
        self.page_mode = "single"
        self._macro_recording = False
        self.steps = []
        self._canvas = types.SimpleNamespace(clear_clone_source=lambda: None)

    def __getattr__(self, name):
        return lambda *args, **kwargs: self.steps.append(name)

    def get_image_indices(self):
        return [0, 1]

    def get_reading_indices(self):
        return [0, 1]

    def _paint_stroke_blocks_command(self):
        return False


@pytest.mark.parametrize("current", [0, 99], ids=["page du document", "page retirée"])
def test_transparency_preview_is_restored_after_the_other_tools_are_reset(current):
    viewer = Navigator(current)
    viewer.navigate(1)
    restore = viewer.steps.index("_restore_transparency_for_page")
    resets = [i for i, step in enumerate(viewer.steps)
              if step.startswith("_reset_") and step.endswith("_preview")]
    assert len(resets) == 6 and restore > max(resets)
    assert restore < viewer.steps.index("display_image")
