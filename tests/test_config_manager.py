import json
import os

from modules.qt.config_manager import ConfigManager, Panel2Config


# ---------------------------------------------------------------------------
# Cycle de vie / fichier
# ---------------------------------------------------------------------------

def test_init_creates_config_file(tmp_path):
    cfg = ConfigManager(config_dir=str(tmp_path))
    assert os.path.exists(cfg.get_config_file_path())


def test_init_with_no_existing_file_uses_defaults(tmp_path):
    cfg = ConfigManager(config_dir=str(tmp_path))
    assert cfg.get_dark_mode() is False
    assert cfg.get_thumbnail_size() == "normal"


def test_save_and_reload_roundtrip(tmp_path):
    cfg1 = ConfigManager(config_dir=str(tmp_path))
    cfg1.set_dark_mode(True)
    cfg1.set_thumbnail_size("large")

    cfg2 = ConfigManager(config_dir=str(tmp_path))
    assert cfg2.get_dark_mode() is True
    assert cfg2.get_thumbnail_size() == "large"


def test_load_merges_new_keys_with_existing_file(tmp_path):
    config_file = tmp_path / ConfigManager.CONFIG_FILENAME
    config_file.write_text(json.dumps({"dark_mode": True}), encoding="utf-8")

    cfg = ConfigManager(config_dir=str(tmp_path))
    # Clé présente dans le fichier existant : préservée
    assert cfg.get_dark_mode() is True
    # Clé absente du fichier existant mais présente dans DEFAULT_CONFIG : ajoutée
    assert cfg.get_thumbnail_size() == "normal"


def test_load_config_corrupted_json_falls_back_to_defaults(tmp_path):
    config_file = tmp_path / ConfigManager.CONFIG_FILENAME
    config_file.write_text("{not valid json", encoding="utf-8")

    cfg = ConfigManager(config_dir=str(tmp_path))
    assert cfg.get_dark_mode() is False  # valeur par défaut, pas de crash


def test_migrate_from_temp_moves_old_config(tmp_path, monkeypatch):
    import tempfile
    old_dir = os.path.join(tempfile.gettempdir(), "MosaicViewTemp")
    os.makedirs(old_dir, exist_ok=True)
    old_config_path = os.path.join(old_dir, ConfigManager.CONFIG_FILENAME)
    with open(old_config_path, "w", encoding="utf-8") as f:
        json.dump({"dark_mode": True}, f)

    new_dir = str(tmp_path / "new_config_location")
    os.makedirs(new_dir, exist_ok=True)
    try:
        cfg = ConfigManager(config_dir=new_dir)
        assert cfg.get_dark_mode() is True
        assert not os.path.exists(old_config_path)  # déplacé, pas copié
    finally:
        if os.path.exists(old_config_path):
            os.remove(old_config_path)


# ---------------------------------------------------------------------------
# get / set génériques
# ---------------------------------------------------------------------------

def test_get_unknown_key_returns_default_param(tmp_path):
    cfg = ConfigManager(config_dir=str(tmp_path))
    assert cfg.get("nonexistent_key", "fallback") == "fallback"


def test_set_without_save_does_not_write_to_disk_immediately(tmp_path):
    cfg = ConfigManager(config_dir=str(tmp_path))
    mtime_before = os.path.getmtime(cfg.get_config_file_path())

    import time
    time.sleep(0.05)
    cfg.set("dark_mode", True, save=False)

    mtime_after = os.path.getmtime(cfg.get_config_file_path())
    assert mtime_before == mtime_after
    assert cfg.get("dark_mode") is True  # en mémoire malgré tout


# ---------------------------------------------------------------------------
# Getters/setters représentatifs
# ---------------------------------------------------------------------------

def test_window_size_roundtrip(tmp_path):
    cfg = ConfigManager(config_dir=str(tmp_path))
    cfg.set_window_size(1024, 768)
    assert cfg.get_window_size() == {"width": 1024, "height": 768}


def test_window_position_roundtrip(tmp_path):
    cfg = ConfigManager(config_dir=str(tmp_path))
    assert cfg.get_window_position() is None
    cfg.set_window_position(100, 200)
    assert cfg.get_window_position() == {"x": 100, "y": 200}


def test_thumbnail_size_rejects_invalid_value(tmp_path):
    cfg = ConfigManager(config_dir=str(tmp_path))
    ok = cfg.set_thumbnail_size("huge")  # pas dans ['small', 'normal', 'large']
    assert ok is False
    assert cfg.get_thumbnail_size() == "normal"  # inchangé


def test_thumbnail_size_accepts_valid_value(tmp_path):
    cfg = ConfigManager(config_dir=str(tmp_path))
    ok = cfg.set_thumbnail_size("small")
    assert ok is True
    assert cfg.get_thumbnail_size() == "small"


def test_split_ratio_roundtrip(tmp_path):
    cfg = ConfigManager(config_dir=str(tmp_path))
    assert cfg.get_split_ratio() == 0.5
    cfg.set_split_ratio(0.3)
    assert cfg.get_split_ratio() == 0.3


def test_renumber_mode_roundtrip(tmp_path):
    cfg = ConfigManager(config_dir=str(tmp_path))
    assert cfg.get_renumber_mode() == 1
    cfg.set_renumber_mode(0)
    assert cfg.get_renumber_mode() == 0


# ---------------------------------------------------------------------------
# Fichiers récents (recent_files / recent_dbs)
# ---------------------------------------------------------------------------

def test_add_recent_file_inserts_at_front(tmp_path):
    cfg = ConfigManager(config_dir=str(tmp_path))
    f1 = str(tmp_path / "a.cbz")
    f2 = str(tmp_path / "b.cbz")
    cfg.add_recent_file(f1)
    cfg.add_recent_file(f2)
    assert cfg.get_recent_files()[0] == os.path.abspath(f2)
    assert cfg.get_recent_files()[1] == os.path.abspath(f1)


def test_add_recent_file_moves_existing_to_front_without_duplicate(tmp_path):
    cfg = ConfigManager(config_dir=str(tmp_path))
    f1 = str(tmp_path / "a.cbz")
    f2 = str(tmp_path / "b.cbz")
    cfg.add_recent_file(f1)
    cfg.add_recent_file(f2)
    cfg.add_recent_file(f1)  # ré-ajout : doit remonter, pas dupliquer
    files = cfg.get_recent_files()
    assert files[0] == os.path.abspath(f1)
    assert files.count(os.path.abspath(f1)) == 1
    assert len(files) == 2


def test_add_recent_file_respects_max_files_limit(tmp_path):
    cfg = ConfigManager(config_dir=str(tmp_path))
    for i in range(15):
        cfg.add_recent_file(str(tmp_path / f"file{i}.cbz"), max_files=10)
    assert len(cfg.get_recent_files()) == 10


def test_clean_recent_files_removes_missing_paths(tmp_path):
    cfg = ConfigManager(config_dir=str(tmp_path))
    existing = tmp_path / "exists.cbz"
    existing.write_bytes(b"data")
    missing = str(tmp_path / "missing.cbz")

    cfg.add_recent_file(str(existing))
    cfg.add_recent_file(missing)
    assert len(cfg.get_recent_files()) == 2

    cfg.clean_recent_files()
    remaining = cfg.get_recent_files()
    assert len(remaining) == 1
    assert os.path.abspath(str(existing)) in remaining


def test_recent_dbs_same_behavior_as_recent_files(tmp_path):
    cfg = ConfigManager(config_dir=str(tmp_path))
    db1 = str(tmp_path / "lib1.mvdb")
    cfg.add_recent_db(db1)
    assert cfg.get_recent_dbs() == [os.path.abspath(db1)]


# ---------------------------------------------------------------------------
# Marque-pages
# ---------------------------------------------------------------------------

def test_bookmark_set_and_get(tmp_path):
    cfg = ConfigManager(config_dir=str(tmp_path))
    filepath = str(tmp_path / "comic.cbz")
    cfg.set_bookmark(filepath, 42)
    assert cfg.get_bookmark(filepath) == 42


def test_bookmark_get_unknown_returns_none(tmp_path):
    cfg = ConfigManager(config_dir=str(tmp_path))
    assert cfg.get_bookmark(str(tmp_path / "unknown.cbz")) is None


def test_bookmark_remove(tmp_path):
    cfg = ConfigManager(config_dir=str(tmp_path))
    filepath = str(tmp_path / "comic.cbz")
    cfg.set_bookmark(filepath, 5)
    cfg.remove_bookmark(filepath)
    assert cfg.get_bookmark(filepath) is None


def test_bookmark_remove_nonexistent_does_not_error(tmp_path):
    cfg = ConfigManager(config_dir=str(tmp_path))
    result = cfg.remove_bookmark(str(tmp_path / "never_set.cbz"))
    assert result is True


def test_has_any_bookmark(tmp_path):
    cfg = ConfigManager(config_dir=str(tmp_path))
    assert cfg.has_any_bookmark() is False
    cfg.set_bookmark(str(tmp_path / "a.cbz"), 1)
    assert cfg.has_any_bookmark() is True


def test_clear_bookmarks(tmp_path):
    cfg = ConfigManager(config_dir=str(tmp_path))
    cfg.set_bookmark(str(tmp_path / "a.cbz"), 1)
    cfg.set_bookmark(str(tmp_path / "b.cbz"), 2)
    cfg.clear_bookmarks()
    assert cfg.get_bookmarks() == {}


# ---------------------------------------------------------------------------
# Clé API ComicVine (chiffrement DPAPI)
# ---------------------------------------------------------------------------

def test_comicvine_api_key_roundtrip_encrypted(tmp_path):
    cfg = ConfigManager(config_dir=str(tmp_path))
    cfg.set_comicvine_api_key("my-secret-api-key-12345")
    assert cfg.get_comicvine_api_key() == "my-secret-api-key-12345"

    # Vérifie que la valeur stockée sur disque n'est PAS en clair
    with open(cfg.get_config_file_path(), encoding="utf-8") as f:
        raw_content = f.read()
    assert "my-secret-api-key-12345" not in raw_content


def test_comicvine_api_key_empty_returns_empty(tmp_path):
    cfg = ConfigManager(config_dir=str(tmp_path))
    assert cfg.get_comicvine_api_key() == ""


def test_comicvine_api_key_migrates_plaintext_legacy_value(tmp_path):
    cfg = ConfigManager(config_dir=str(tmp_path))
    # Simule une ancienne valeur stockée en clair (avant le chiffrement DPAPI)
    cfg.set("comicvine_api_key", "legacy-plaintext-key", save=True)

    # Premier accès : détecte que ce n'est pas du DPAPI valide, retourne la valeur
    # en clair ET la rechiffre immédiatement pour la suite.
    result = cfg.get_comicvine_api_key()
    assert result == "legacy-plaintext-key"

    with open(cfg.get_config_file_path(), encoding="utf-8") as f:
        raw_content = f.read()
    assert "legacy-plaintext-key" not in raw_content


# ---------------------------------------------------------------------------
# Config barre d'icônes (fichier séparé)
# ---------------------------------------------------------------------------

def test_icon_toolbar_layout_roundtrip(tmp_path):
    cfg = ConfigManager(config_dir=str(tmp_path))
    assert cfg.get_icon_toolbar_layout() is None
    cfg.set_icon_toolbar_layout(["zoom", "rotate", "crop"])
    assert cfg.get_icon_toolbar_layout() == ["zoom", "rotate", "crop"]


def test_icon_size_index_default_and_roundtrip(tmp_path):
    cfg = ConfigManager(config_dir=str(tmp_path))
    assert cfg.get_icon_size_index() == 0
    cfg.set_icon_size_index(2)
    assert cfg.get_icon_size_index() == 2


# ---------------------------------------------------------------------------
# Panel2Config — redirige vers les clés *_panel2
# ---------------------------------------------------------------------------

def test_panel2config_does_not_overwrite_panel1_keys(tmp_path):
    cfg = ConfigManager(config_dir=str(tmp_path))
    panel2 = Panel2Config(cfg)

    cfg.set_icon_size_index(1)          # panel1
    panel2.set_icon_size_index(2)       # panel2

    assert cfg.get_icon_size_index() == 1
    assert panel2.get_icon_size_index() == 2


def test_panel2config_renumber_mode_isolated_from_panel1(tmp_path):
    cfg = ConfigManager(config_dir=str(tmp_path))
    panel2 = Panel2Config(cfg)

    cfg.set_renumber_mode(0)
    panel2.set_renumber_mode(2)

    assert cfg.get_renumber_mode() == 0
    assert panel2.get_renumber_mode() == 2
    assert cfg.get_renumber_mode_panel2() == 2
