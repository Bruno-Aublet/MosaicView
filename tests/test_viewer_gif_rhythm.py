"""Rythme de lecture d'une image animée dans la visionneuse (ImageViewer) : chaque
image reste affichée le temps de son propre délai, et non de celui de la
suivante, qui déplaçait chaque pause sur l'image d'avant. Un délai de 0 ou de
10 ms dure 100 ms à l'affichage.

image_viewer_qt.py importe PySide6 : les méthodes sont extraites par AST, le
temps est simulé par une fausse minuterie, et l'écran est remplacé.
"""
import types

import pytest

import ast_loader
from modules.qt.entries import animation_play_duration, gif_playback_delay

Methods = ast_loader.load_methods(
    "image_viewer_qt.py", "ImageViewer",
    {"toggle_gif_playback", "_schedule_gif_frame", "_animate_gif_frame"},
    gif_playback_delay=gif_playback_delay,
    get_gif_frame=lambda entry, index: types.SimpleNamespace(
        index=index, size=(8, 8), mode="RGB", info={}),
    _compose_on_checkerboard=lambda frame: frame,
    _pil_to_qpixmap=lambda frame: frame,
    _state_module=types.SimpleNamespace(state=None))


class Viewer(Methods):
    def __init__(self, durations, play_count=None):
        self.is_animated_gif = True
        self.gif_is_playing = False
        self.gif_current_frame = 0
        self.gif_durations = durations
        self._gif_plays_done = 0
        self._gif_play_count = play_count
        self.zoom_level = 1.0
        self.page_mode = "single"
        self.current_idx = 0
        state = types.SimpleNamespace(images_data=[{"gif_frame_count": len(durations)}])
        self.callbacks = {"state": state}
        self.shown = []
        self.delays = []
        self._gif_timer = types.SimpleNamespace(start=self.delays.append, stop=lambda: None)
        self._play_pause_btn = types.SimpleNamespace(setText=lambda text: None)
        self._canvas = types.SimpleNamespace(
            width=lambda: 800, height=lambda: 600, pan_offset_x=0, pan_offset_y=0,
            set_pixmap_and_geometry=lambda pixmap, *geometry: self.shown.append(pixmap.index))

    def play(self, ticks):
        """Lance la lecture, puis laisse la minuterie sonner `ticks` fois."""
        self.toggle_gif_playback()
        for _tick in range(ticks):
            self._animate_gif_frame()


def test_each_image_stays_for_its_own_delay():
    """Une pause de 3 secondes sur l'image 2 restait sur l'image 1."""
    viewer = Viewer([300, 3000, 300, 300])
    viewer.play(3)
    assert viewer.shown == [0, 1, 2, 3]
    assert viewer.delays == [300, 3000, 300, 300]


def test_rhythm_stays_right_after_a_full_turn():
    viewer = Viewer([300, 3000])
    viewer.play(3)
    assert viewer.shown == [0, 1, 0, 1]
    assert viewer.delays == [300, 3000, 300, 3000]


def test_animation_played_once_stops_on_its_last_image():
    viewer = Viewer([300, 3000], play_count=1)
    viewer.play(1)
    assert viewer.shown == [0, 1]
    assert viewer.gif_is_playing is False and viewer.gif_current_frame == 0
    assert viewer.delays == [300]


@pytest.mark.parametrize("duration, shown_for", [(0, 100), (10, 100), (20, 20), (800, 800)])
def test_very_short_delay_lasts_100_ms(duration, shown_for):
    assert gif_playback_delay(duration) == shown_for


@pytest.mark.parametrize("durations, expected", [
    ([300, 3000], 3300), ([0, 0, 0], 300), ([10, 500], 600), ([], None)])
def test_duration_in_the_mosaic_tooltip_is_the_one_played_by_the_viewer(durations, expected):
    """Des images de 0 ms donneraient 0 s pour une animation visible une
    fraction de seconde."""
    assert animation_play_duration({"gif_durations": durations}) == expected
    assert animation_play_duration({}) is None
