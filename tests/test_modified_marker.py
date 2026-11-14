"""Marqueur « modifié » de l'onglet mosaïque : AppState.modified prévient ses
écoutes (TabBar.follow_modified) à chaque changement de valeur, et à lui seul."""

from modules.qt.state import AppState, THEMES


def test_listener_called_only_when_value_changes():
    st = AppState()
    calls = []
    st.add_modified_listener(lambda: calls.append(st.modified))
    st.modified = False
    assert calls == []
    st.modified = True
    st.modified = True
    st.modified = False
    assert calls == [True, False]


def test_value_is_stored_as_bool():
    st = AppState()
    st.modified = 1
    assert st.modified is True
    st.modified = None
    assert st.modified is False


def test_removed_listener_no_longer_called():
    st = AppState()
    calls = []
    fn = lambda: calls.append(1)  # noqa: E731
    st.add_modified_listener(fn)
    st.remove_modified_listener(fn)
    st.modified = True
    assert calls == []


def test_failing_listener_never_breaks_the_assignment(monkeypatch):
    import modules.qt.error_log as error_log
    logged = []
    monkeypatch.setattr(error_log, "log_exception", lambda e, *a, **k: logged.append(e))
    st = AppState()
    after = []

    def boom():
        raise RuntimeError("widget détruit")

    st.add_modified_listener(boom)
    st.add_modified_listener(lambda: after.append(st.modified))
    st.modified = True
    assert st.modified is True
    assert after == [True]
    assert len(logged) == 1


def test_panels_are_independent():
    p1, p2 = AppState(), AppState()
    calls = []
    p2.add_modified_listener(lambda: calls.append(1))
    p1.modified = True
    assert calls == []


def test_both_themes_define_the_marker_color():
    assert all("modified_mark" in theme for theme in THEMES.values())
