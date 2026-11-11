"""Règles de la fenêtre de choix d'un fichier ou d'un dossier (file_picker.py,
sans Qt) : point de départ, chemin tapé, noms multiples, filtres et
extension ajoutée, dossier parent, historique, nom d'un nouveau dossier."""
import os

from modules.qt.file_picker import (
    COMPUTER, NavigationHistory, default_extension, drive_display, existing_ancestor,
    join_names, matches, open_file_under, parent_folder, resolve, split_names, start_location,
    unique_folder_name, with_extension,
)


# --- Filtres -------------------------------------------------------------------

def test_matches_ignores_case():
    assert matches("Aaaa.CBZ", ("*.cbz",))
    assert matches("Aaaa.cbr", ("*.cbz", "*.cbr"))
    assert not matches("Aaaa.zip", ("*.cbz",))
    assert matches("sans_extension", ("*",))


def test_default_extension_is_the_first_plain_pattern():
    assert default_extension(("*.cbz",)) == ".cbz"
    assert default_extension(("*", "*.ttf")) == ".ttf"
    assert default_extension(("*",)) is None
    assert default_extension(("*.jp*",)) is None


def test_extension_added_only_when_missing():
    assert with_extension("Aaaa", ("*.cbz",)) == "Aaaa.cbz"
    assert with_extension("Aaaa.CBZ", ("*.cbz",)) == "Aaaa.CBZ"
    # Comme la boîte de Windows : une autre extension n'est pas remplacée
    assert with_extension("Aaaa.txt", ("*.cbz",)) == "Aaaa.txt.cbz"
    assert with_extension("Aaaa", ("*",)) == "Aaaa"


# --- Noms multiples ------------------------------------------------------------

def test_split_and_join_names():
    assert split_names('"Aaaa 01.cbz" "Bbbb.cbr"') == ["Aaaa 01.cbz", "Bbbb.cbr"]
    assert split_names("  Aaaa 01.cbz ") == ["Aaaa 01.cbz"]
    assert split_names("   ") == []
    assert split_names('"" "Aaaa.cbz"') == ["Aaaa.cbz"]
    assert join_names(["Aaaa.cbz"]) == "Aaaa.cbz"
    assert join_names(["Aaaa.cbz", "Bbbb.cbz"]) == '"Aaaa.cbz" "Bbbb.cbz"'
    assert split_names(join_names(["Aaaa 1.cbz", "Bbbb 2.cbz"])) == ["Aaaa 1.cbz", "Bbbb 2.cbz"]


# --- Chemin tapé ---------------------------------------------------------------

def test_resolve_relative_and_absolute(tmp_path):
    current = str(tmp_path)
    assert resolve(current, "Aaaa.cbz") == os.path.join(current, "Aaaa.cbz")
    assert resolve(current, r"..\Bbbb") == os.path.normpath(os.path.join(current, "..", "Bbbb"))
    other = str(tmp_path / "Cccc")
    assert resolve(current, other) == other
    assert resolve(current, "   ") == ""


def test_resolve_drive_letter_alone_is_its_root():
    assert resolve(r"D:\Aaaa", "C:") == "C:\\"


def test_resolve_expands_environment_variables(tmp_path, monkeypatch):
    monkeypatch.setenv("MV_TEST_DIR", str(tmp_path))
    assert resolve("", r"%MV_TEST_DIR%\Aaaa") == os.path.join(str(tmp_path), "Aaaa")


# --- Point de départ -----------------------------------------------------------

def test_start_location(tmp_path):
    home = str(tmp_path / "home")
    folder = tmp_path / "Aaaa"
    folder.mkdir()
    (folder / "Bbbb.cbz").write_bytes(b"")
    assert start_location("", home) == (home, "")
    assert start_location(str(folder), home) == (str(folder), "")
    # Fichier existant ou nom proposé : dossier + nom pré-rempli
    assert start_location(str(folder / "Bbbb.cbz"), home) == (str(folder), "Bbbb.cbz")
    assert start_location(str(folder / "Neuf.cbz"), home) == (str(folder), "Neuf.cbz")
    # Nom seul (sans dossier) : dossier de départ par défaut
    assert start_location("Aaaa.xlsx", home) == (home, "Aaaa.xlsx")


def test_start_location_of_a_vanished_folder(tmp_path):
    home = str(tmp_path / "home")
    gone = tmp_path / "Aaaa" / "Disparu"
    (tmp_path / "Aaaa").mkdir()
    # Choix d'un dossier : jamais pris pour un nom de fichier
    assert start_location(str(gone), home, directory_only=True) == (str(tmp_path / "Aaaa"), "")
    assert existing_ancestor(str(gone / "x" / "y")) == str(tmp_path / "Aaaa")


# --- Dossier parent ------------------------------------------------------------

def test_parent_folder_up_to_the_drives():
    assert parent_folder(r"C:\Aaaa\Bbbb") == r"C:\Aaaa"
    assert parent_folder(r"C:\Aaaa") == "C:\\"
    assert parent_folder("C:\\") == COMPUTER
    assert parent_folder(r"\\serveur\partage") == COMPUTER
    assert parent_folder(COMPUTER) is None


# --- Historique ----------------------------------------------------------------

def test_navigation_history():
    history = NavigationHistory()
    history.visit("A")
    assert not history.can_go_back()
    history.visit("B")
    history.visit("C")
    history.visit("C")  # même dossier : rien de plus dans l'historique
    assert history.back() == "B"
    assert history.back() == "A"
    assert history.back() is None
    assert history.forward() == "B"
    # Une nouvelle visite vide la liste Suivant
    history.visit("D")
    assert not history.can_go_forward()
    assert history.back() == "B"
    assert history.current == "B"


# --- Comic ouvert dans un panneau ----------------------------------------------

def test_open_comic_or_its_folder_is_found(tmp_path):
    """Renommer ou supprimer le comic ouvert, ou un dossier qui le contient,
    est refusé ; un voisin au nom proche ne l'est pas."""
    folder = tmp_path / "Aaaa"
    opened = str(folder / "Bbbb 01.cbz")
    assert open_file_under(opened, [opened]) == opened
    assert open_file_under(opened.upper(), [opened]) == opened
    assert open_file_under(str(folder), [opened]) == opened
    assert open_file_under(str(tmp_path), [opened]) == opened
    assert open_file_under(str(tmp_path / "Aaaa 2"), [opened]) is None
    assert open_file_under(str(folder / "Bbbb 02.cbz"), [opened]) is None
    assert open_file_under(opened, []) is None


# --- Lecteurs ------------------------------------------------------------------

def test_drive_shown_with_its_name_when_there_is_one():
    assert drive_display("C:\\", "Aaaa") == "Aaaa (C:)"
    assert drive_display("C:\\", "") == "C:\\"
    assert drive_display("D:\\", "  ") == "D:\\"
    # Nom venu du disque : pas de caractère de contrôle (fausse ligne)
    assert drive_display("E:\\", "Aa\naa\x07") == "Aaaa (E:)"
    assert drive_display("Z:\\", "\\\\serveur\\partage") == "\\\\serveur\\partage (Z:)"


# --- Nouveau dossier -----------------------------------------------------------

def test_unique_folder_name():
    assert unique_folder_name(["Aaaa"], "Nouveau dossier") == "Nouveau dossier"
    assert unique_folder_name(["nouveau DOSSIER"], "Nouveau dossier") == "Nouveau dossier (2)"
    assert unique_folder_name(["Nouveau dossier", "Nouveau dossier (2)"],
                              "Nouveau dossier") == "Nouveau dossier (3)"
