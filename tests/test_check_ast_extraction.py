"""Extraction de code par AST : seulement par tests/ast_loader.py.

Une extraction écrite à la main (ast.Module compilé puis exécuté) ne contrôle
pas les dépendances de ce qu'elle extrait : une dépendance ajoutée plus tard
au module lève NameError à l'exécution, qu'un `except` de repli peut avaler,
et le test vérifie alors le repli sans le savoir. ast_loader.load /
load_methods font échouer le test en nommant ce qui manque.

LEGACY liste les fichiers qui font encore leur propre extraction. Elle ne
peut que raccourcir : un nouveau fichier qui en fait une échoue ici, et un
fichier migré vers ast_loader doit en être retiré.
"""
import re
from pathlib import Path

TESTS = Path(__file__).resolve().parent
HAND_EXTRACTION = re.compile(r"ast\.Module\(|exec\(compile")

LEGACY = {
    "test_7z_member_extraction.py",
    "test_additions_undo_point.py",
    "test_altered_pages.py",
    "test_archive_member_reading.py",
    "test_audit7_p3.py",
    "test_batch_copy_pages.py",
    "test_batch_logs_latin.py",
    "test_batch_metadata_cancel.py",
    "test_batch_metadata_comicinfo.py",
    "test_batch_metadata_running.py",
    "test_batch_metadata_save.py",
    "test_batch_unique_flat_name.py",
    "test_batch_zip_writer.py",
    "test_check_dialogs_destroyed.py",
    "test_check_operation_end.py",
    "test_clipboard_unreadable_pages.py",
    "test_comicvine_series_part.py",
    "test_error_log.py",
    "test_error_reporting.py",
    "test_gif_frame_extraction.py",
    "test_library_deferred_db_actions.py",
    "test_loaded_pages_install.py",
    "test_merge_dialog_memory.py",
    "test_operation_running.py",
    "test_parent_dir_segment.py",
    "test_pdf_channel_reader.py",
    "test_resource_export_copy.py",
    "test_save_dpi_bytes.py",
    "test_untrusted_names.py",
    "test_viewer_page_work.py",
}


def _hand_extractors():
    own = Path(__file__).name
    return {p.name for p in TESTS.glob("*.py")
            if p.name not in ("ast_loader.py", own)
            and HAND_EXTRACTION.search(p.read_text(encoding="utf-8"))}


def test_new_tests_extract_with_ast_loader():
    new = sorted(_hand_extractors() - LEGACY)
    assert not new, ("extraire par ast_loader.load / load_methods, pas à la main :\n"
                     + "\n".join(new))


def test_migrated_files_leave_the_legacy_list():
    migrated = sorted(LEGACY - _hand_extractors())
    assert not migrated, ("plus d'extraction à la main : retirer de LEGACY :\n"
                          + "\n".join(migrated))
