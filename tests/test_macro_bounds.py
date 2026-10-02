import io
from types import SimpleNamespace

from PIL import Image

from modules.qt.macro_engine import apply_step_to_entry, step_fits_page


def make_png(w, h):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (255, 255, 255)).save(buf, format="PNG")
    return buf.getvalue()


def make_viewer(page_w, page_h):
    """Faux viewer : page courante de page_w × page_h, chaque perform_*
    appelé est tracé dans viewer.calls."""
    calls = []

    def record(name):
        def _fn(*args, **kwargs):
            calls.append((name, args, kwargs))
            return True
        return _fn

    state = SimpleNamespace(images_data=[{"bytes": make_png(page_w, page_h)}])
    return SimpleNamespace(
        callbacks={"state": state},
        current_idx=0,
        calls=calls,
        perform_clone_step=record("clone"),
        perform_blur_step=record("blur"),
        perform_text_step=record("text"),
        perform_shapes_step=record("shapes"),
        perform_paste_image_step=record("paste_image"),
        perform_auto_straighten=record("straighten_auto"),
    )


def blur_step(points, ref=(200, 200)):
    params = {"brush_diam_px": 10, "strength": 5, "points_px": points}
    if ref is not None:
        params["ref_w"], params["ref_h"] = ref
    return {"tool": "blur", "params": params}


# ── Points peints (clonage/flou) ─────────────────────────────────────────────

def test_point_inside_reference_but_outside_target_fails():
    assert not step_fits_page(blur_step([[50, 50], [150, 50]]), 100, 100)


def test_same_size_page_fits():
    assert step_fits_page(blur_step([[50, 50], [150, 150]]), 200, 200)


def test_overflow_already_present_at_recording_is_tolerated():
    # x=250 débordait déjà de la page de référence (200 px) à l'enregistrement.
    assert step_fits_page(blur_step([[50, 50], [250, 50]]), 200, 200)


def test_legacy_step_without_reference_fails_only_when_entirely_outside():
    assert step_fits_page(blur_step([[50, 50], [150, 50]], ref=None), 100, 100)
    assert not step_fits_page(blur_step([[150, 150], [180, 150]], ref=None), 100, 100)


def test_clone_source_outside_target_fails():
    step = {"tool": "clone", "params": {
        "mode": "fixed", "brush_diam_px": 10,
        "source_px": [150, 20], "points_px": [[20, 20], [30, 20]],
        "ref_w": 200, "ref_h": 200,
    }}
    assert not step_fits_page(step, 100, 100)
    assert step_fits_page(step, 200, 200)


# ── Texte, formes, images collées ───────────────────────────────────────────

def test_text_anchor_outside_target_fails():
    step = {"tool": "text", "params": {
        "blocks": [{"html": "", "img_x": 150, "img_y": 40, "top_y_offset_img": 10}],
        "ref_w": 200, "ref_h": 200,
    }}
    assert not step_fits_page(step, 100, 100)
    assert step_fits_page(step, 200, 200)


def test_shape_box_exceeding_target_fails():
    step = {"tool": "shapes", "params": {
        "shapes": [{"shape_type": "rect", "ix1": 90, "iy1": 10, "ix2": 20, "iy2": 60,
                    "color": "#ff0000", "fill_enabled": False, "thickness": 2}],
        "ref_w": 200, "ref_h": 200,
    }}
    assert step_fits_page(step, 100, 100)
    assert not step_fits_page(step, 80, 100)


def test_pasted_image_box_partially_outside_reference_is_tolerated():
    step = {"tool": "paste_image", "params": {
        "images": [{"png_b64": "", "ix1": 150, "iy1": 150, "ix2": 260, "iy2": 260}],
        "ref_w": 200, "ref_h": 200,
    }}
    assert step_fits_page(step, 200, 200)
    assert not step_fits_page(step, 180, 200)


# ── Câblage dans apply_step_to_entry ────────────────────────────────────────

def test_step_not_fitting_is_not_replayed():
    viewer = make_viewer(100, 100)
    assert apply_step_to_entry(viewer, blur_step([[50, 50], [150, 50]])) is False
    assert viewer.calls == []


def test_step_fitting_is_replayed():
    viewer = make_viewer(200, 200)
    assert apply_step_to_entry(viewer, blur_step([[50, 50], [150, 50]])) is True
    assert [c[0] for c in viewer.calls] == ["blur"]


def test_auto_straighten_accepts_already_straight_page():
    viewer = make_viewer(100, 100)
    apply_step_to_entry(viewer, {"tool": "straighten_auto", "params": {}})
    name, _args, kwargs = viewer.calls[0]
    assert name == "straighten_auto"
    assert kwargs.get("no_skew_ok") is True
