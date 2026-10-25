"""Journal envoyé par mail (journal des erreurs, journal des numérisations) :
utils.copy_log_for_mail.

Le journal est copié dans le presse-papiers et le client mail s'ouvre avec une
consigne de collage ; le curseur y est placé au début du corps, devant la
consigne. Vérifié :
- le texte copié finit par exactement deux retours à la ligne, quels que
  soient ceux qui terminent déjà le journal : le journal collé ne touche pas
  la consigne ;
- toute fonction de modules/qt/ qui ouvre un lien mailto: avec un corps
  copie par copy_log_for_mail, jamais par _copy_to_clipboard (garde-fou AST).

utils.py importe PySide6 dans _copy_to_clipboard : la fonction est extraite
par AST (tests/ast_loader.py), avec un faux presse-papiers.
"""
import ast

import pytest

import ast_loader


@pytest.mark.parametrize("log", [
    "ligne 1\nligne 2",
    "ligne 1\nligne 2\n",
    "ligne 1\nligne 2\n\n\n",
    "ligne 1\r\nligne 2\r\n",
])
def test_copied_log_ends_with_blank_line(log):
    copied = []
    ns = ast_loader.load("utils.py", ["copy_log_for_mail"],
                         _copy_to_clipboard=copied.append)
    ns["copy_log_for_mail"](log)
    assert copied == [log.rstrip("\r\n") + "\n\n"]


def _mail_senders():
    """(fichier, fonction) de modules/qt/ dont le texte contient un lien
    mailto: avec un corps (body=)."""
    found = []
    for path in sorted(ast_loader.QT_DIR.glob("*.py")):
        tree = ast_loader.module_tree(path)
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            text = "".join(node.value for node in ast.walk(func)
                           if isinstance(node, ast.Constant) and isinstance(node.value, str))
            if "mailto:" in text and "body=" in text:
                found.append((path.name, func))
    return found


def _called_names(func):
    return {node.func.id if isinstance(node.func, ast.Name) else node.func.attr
            for node in ast.walk(func)
            if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute))}


def test_mail_senders_copy_log_for_mail():
    senders = _mail_senders()
    names = {func.name for _file, func in senders}
    # Le scan doit au moins retrouver les deux envois connus
    assert {"send_error_log_by_mail", "_send_scan_log_by_mail"} <= names
    wrong = [f"{file}::{func.name}" for file, func in senders
             if "copy_log_for_mail" not in _called_names(func)
             or "_copy_to_clipboard" in _called_names(func)]
    assert not wrong, ("journal envoyé par mail copié sans copy_log_for_mail "
                       f"(collé contre la consigne) : {wrong}")
