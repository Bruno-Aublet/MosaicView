# temp_files.py — Gestion des fichiers temporaires (version Qt, sans tkinter)

import os
import sys
import tempfile
import time
import shutil

from modules.qt.localization import _


def cleanup_stale_mei_dirs():
    """
    Supprime les dossiers _MEI* orphelins laissés par PyInstaller (mode --onefile)
    après un plantage. Le dossier de l'instance courante (sys._MEIPASS) est exclu.
    Les dossiers encore verrouillés par une autre instance active sont ignorés.
    Sans effet hors PyInstaller.
    """
    current_meipass = getattr(sys, "_MEIPASS", None)
    if current_meipass is None:
        return  # pas en mode PyInstaller onefile

    temp_base = tempfile.gettempdir()
    try:
        entries = os.listdir(temp_base)
    except Exception:
        return

    for name in entries:
        if not name.startswith("_MEI"):
            continue
        path = os.path.join(temp_base, name)
        if not os.path.isdir(path):
            continue
        if os.path.normcase(os.path.realpath(path)) == os.path.normcase(os.path.realpath(current_meipass)):
            continue  # dossier de l'instance courante
        # Teste si le dossier est verrouillé par une autre instance active
        try:
            os.rename(path, path)
        except (PermissionError, OSError):
            continue  # verrouillé → autre instance active, on laisse
        try:
            shutil.rmtree(path, ignore_errors=True)
        except Exception:
            pass  # sans conséquence : dossier temporaire retenté au prochain démarrage


def cleanup_legacy_root_clipboard_dirs():
    """
    Supprime les dossiers `clipboard_*` laissés à la racine de `%TEMP%` par les
    versions antérieures au correctif de `PanelWidget._get_temp_dir()`, qui les
    écrivait hors de `MosaicViewTemp` et donc hors de portée de tout nettoyage
    automatique. Sans condition d'âge : leur seule présence ici prouve qu'ils
    sont orphelins, plus aucune version ne peut encore les créer à cet endroit.
    """
    temp_base = tempfile.gettempdir()
    try:
        entries = os.listdir(temp_base)
    except Exception:
        return

    import re
    for name in entries:
        # Seulement le nom exact que MosaicView donnait à ces dossiers
        # (clipboard_<id>_<horodatage>) : %TEMP% est partagé par tous les
        # logiciels, et un simple préfixe effacerait les dossiers d'un autre.
        if not re.fullmatch(r"clipboard_\d+_\d+", name):
            continue
        path = os.path.join(temp_base, name)
        if not os.path.isdir(path):
            continue
        try:
            shutil.rmtree(path, ignore_errors=True)
        except Exception:
            pass  # sans conséquence : dossier temporaire retenté au prochain démarrage


def get_mosaicview_temp_dir():
    """Retourne le chemin du répertoire temporaire centralisé de MosaicView."""
    temp_base = tempfile.gettempdir()
    mosaicview_temp = os.path.join(temp_base, "MosaicViewTemp")
    if not os.path.exists(mosaicview_temp):
        os.makedirs(mosaicview_temp, exist_ok=True)
    return mosaicview_temp


def unique_log_path(directory, kind, now):
    """Chemin libre pour le journal d'un traitement par lot :
    `Log_<kind>_AAAA_MM_JJ_HH_MM.txt` dans `directory`, suivi de `_2`, `_3`…
    si ce nom existe déjà. Le nom n'est daté qu'à la minute : sans suffixe,
    deux lots du même type terminés dans la même minute écriraient dans le
    même fichier, et le résumé du premier renverrait au journal du second."""
    base = f"Log_{kind}_{now.strftime('%Y_%m_%d_%H_%M')}"
    path = os.path.join(directory, base + ".txt")
    n = 2
    while os.path.exists(path):
        path = os.path.join(directory, f"{base}_{n}.txt")
        n += 1
    return path


def log_text(text):
    """`text` sur une seule ligne de journal : chaque caractère de contrôle
    (retour à la ligne, retour chariot…) est écrit sous sa forme échappée
    (« \\n », « \\x07 »). Un nom de membre d'archive cité dans une erreur peut
    en contenir : écrit tel quel, il couperait la ligne et créerait une fausse
    ligne dans le journal."""
    return "".join(c.encode("unicode_escape").decode("ascii") if ord(c) < 32 else c
                   for c in str(text))


def exception_text(exc):
    """Message d'une exception pour un journal ou une fenêtre : le nom de son
    type quand elle n'a pas de message (EOFError, MemoryError), sinon la ligne
    d'erreur resterait vide."""
    return str(exc) or type(exc).__name__


# Âge au-delà duquel un journal (Log_*.txt) est supprimé par le nettoyage de
# la fermeture (cleanup_all_temp_files(keep_logs=True))
LOG_MAX_AGE_DAYS = 30


def cleanup_all_temp_files(keep_logs=False):
    """Supprime le contenu du répertoire temporaire MosaicViewTemp.

    Rend les éléments non effacés, [(nom, message brut de l'erreur)] (élément
    encore utilisé, droits) : le nettoyage automatique de la fermeture les
    ignore (repris au nettoyage suivant), le bouton du mode d'emploi les
    signale au lieu d'annoncer un succès. Un dossier du presse-papiers récent,
    laissé exprès, n'en fait pas partie."""
    failures = []
    try:
        temp_base = tempfile.gettempdir()
        mosaicview_temp = os.path.join(temp_base, "MosaicViewTemp")

        if os.path.exists(mosaicview_temp):
            clipboard_max_age = 12 * 60 * 60
            current_time = time.time()

            for item in os.listdir(mosaicview_temp):
                item_path = os.path.join(mosaicview_temp, item)

                # Les journaux (lots, Log_scan.txt) survivent à la fermeture :
                # le résumé d'un lot indique le chemin du sien. Seuls ceux de
                # plus de LOG_MAX_AGE_DAYS jours sont supprimés, sans quoi un
                # fichier par lot s'accumulerait sans limite. Un préfixe
                # commun plutôt qu'une liste de types, pour qu'un nouveau lot
                # n'ait rien à déclarer ici.
                if keep_logs and item.startswith("Log_") and item.endswith(".txt"):
                    try:
                        age = current_time - os.path.getmtime(item_path)
                    except OSError:
                        continue  # âge illisible : journal gardé
                    if age < LOG_MAX_AGE_DAYS * 24 * 60 * 60:
                        continue

                if item.startswith("clipboard_") and os.path.isdir(item_path):
                    try:
                        age = current_time - os.path.getmtime(item_path)
                        if age < clipboard_max_age:
                            continue
                    except Exception:
                        pass  # repli : âge illisible, dossier traité comme périmé

                try:
                    if os.path.isfile(item_path) or os.path.islink(item_path):
                        os.unlink(item_path)
                    elif os.path.isdir(item_path):
                        shutil.rmtree(item_path)
                except Exception as e:
                    from modules.qt.error_log import log_if_unexpected
                    log_if_unexpected(e)
                    failures.append((item, str(e)))
    except Exception as e:
        from modules.qt.error_log import log_if_unexpected
        log_if_unexpected(e)
        failures.append(("MosaicViewTemp", str(e)))
    return failures


