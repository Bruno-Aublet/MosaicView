---
name: explorer-select
description: Ouvrir l'Explorateur Windows avec le focus sur un fichier précis (pas juste ouvrir son dossier) dans MosaicView. Utiliser dès qu'un nouveau bouton/lien "Ouvrir l'emplacement" ou "Afficher dans l'Explorateur" doit être ajouté — ne jamais réimplémenter un appel explorer /select ad hoc.
---

# Ouvrir l'Explorateur avec focus sur un fichier — MosaicView

Ouvrir l'Explorateur Windows avec le focus sur un fichier précis est non trivial : la commande `explorer /select,"path"` avec `shell=True` fonctionne mais échoue de façon aléatoire (ouvre le dossier sans sélectionner le fichier) quand Explorer n'est pas encore lancé.

## Toujours réutiliser `_explorer_select(path)`

Fonction centralisée : `modules/qt/library_window.py::_explorer_select(path)`. **Ne jamais réimplémenter un appel `explorer /select` ad hoc ailleurs** — toujours importer et appeler cette fonction :
```python
from modules.qt.library_window import _explorer_select
_explorer_select(path.replace('/', '\\'))
```
Le chemin doit être passé en backslashes Windows (`.replace('/', '\\')`) — fait par l'appelant, pas par `_explorer_select` elle-même.

## Implémentation actuelle

`SHOpenFolderAndSelectItems` via `ctypes`, dans un thread dédié avec `CoInitialize`/`CoUninitialize` (requis : appel COM, le thread doit être STA). Double appel avec délai de 600ms pour forcer la sélection quand Explorer vient de s'ouvrir. Fallback vers `subprocess.Popen(['explorer', f'/select,{path}'], shell=False)` (liste d'arguments, pas une chaîne shell) si l'appel COM échoue.

```python
def _explorer_select(path: str):
    import ctypes, threading, time
    def _run():
        ctypes.windll.ole32.CoInitialize(None)
        try:
            shell32 = ctypes.windll.shell32
            # restype/argtypes explicites : évite la troncature 32 bits du pointeur
            # PIDL sur Windows 64 bits (sinon SHOpenFolderAndSelectItems reçoit un
            # pointeur tronqué et échoue silencieusement)
            shell32.ILCreateFromPathW.restype = ctypes.c_void_p
            shell32.ILFree.argtypes = [ctypes.c_void_p]
            shell32.SHOpenFolderAndSelectItems.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_ulong]
            def _select():
                pidl = shell32.ILCreateFromPathW(path)
                if pidl:
                    shell32.SHOpenFolderAndSelectItems(pidl, 0, None, 0)
                    shell32.ILFree(pidl)
                    return True
                return False
            if not _select():
                subprocess.Popen(['explorer', f'/select,{path}'], shell=False)
                return
            time.sleep(0.6)
            _select()
        except Exception:
            subprocess.Popen(['explorer', f'/select,{path}'], shell=False)
        finally:
            ctypes.windll.ole32.CoUninitialize()
    threading.Thread(target=_run, daemon=True).start()
```

## Pourquoi cette forme précise

- `explorer /select,` avec `shell=True` : échoue (pas de focus) quand Explorer n'est pas déjà ouvert.
- `subprocess.Popen(['explorer', '/select,' + path])` sans `shell` seul, sans le double appel COM : n'ouvre même pas le répertoire de façon fiable.
- `SHOpenFolderAndSelectItems` seul sans thread STA (`CoInitialize`) : échoue silencieusement selon l'état COM du thread appelant.
- Double appel avec `time.sleep(0.6)` : nécessaire car au 1er appel Explorer est encore en cours d'initialisation.
- `restype`/`argtypes` explicites sur les fonctions `ctypes` : sans ça, le pointeur PIDL peut être tronqué sur Windows 64 bits et l'appel échoue silencieusement.
- **Piège vécu** : `user_guide_qt.py::_ExportSuccessDialog._open_explorer` utilisait avant correction `subprocess.run(['explorer', f'/select,{path}'], shell=False)` en appel direct (sans passer par `_explorer_select`) — le lien de la fenêtre de résumé d'export ouvrait le dossier par défaut d'Explorer ("Documents") au lieu du dossier de destination réel. Toujours vérifier qu'un nouveau bouton "ouvrir l'emplacement"/"afficher dans l'Explorateur" appelle bien `_explorer_select`, jamais un `subprocess` direct.

## Points d'appel existants (pour référence, avant d'en ajouter un nouveau)

- `library_window.py::_preview_open_in_explorer` (aperçu bibliothèque)
- `library_window.py` — au moins deux autres sites internes (menu contextuel liste, lien de chemin)
- `library_dialogs.py::_open_in_explorer` (confirmation de suppression de base)
- `user_guide_qt.py::_ExportSuccessDialog._open_explorer` (dialogues de succès d'export : polices pIqaD/Tengwar, icônes, son)

## Écarts connus (réimplémentations à corriger, pas à copier)

- **`file_operations_qt.py::_open_file_location`** (skill `save-export`) — réimplémente un appel `explorer /select` direct via `subprocess.Popen` au lieu d'appeler `_explorer_select()`. Repéré lors de la création du skill `save-export`, pas encore corrigé — ne pas copier ce call-site comme modèle.
