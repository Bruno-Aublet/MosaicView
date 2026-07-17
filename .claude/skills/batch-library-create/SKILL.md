---
name: batch-library-create
description: Localiser ou modifier la création d'une Bibliothèque à partir de dossiers déposés (drag & drop uniquement, pré-remplit la nouvelle base .mvdb et lance le scan). Utiliser dès qu'une tâche touche à _make_batch_library (drop_handler_qt.py) ou NewDbDialog avec preset_dir.
---

# Création de bibliothèque en lot — MosaicView

Un des 8 traitements par lot du projet (skill `batch-processing`, **à lire en premier** pour l'architecture commune). **Le plus atypique des 8** : ne vit dans **aucun** des 3 fichiers batch dédiés (`batch_dialogs_qt.py`, `batch_metadata_dialog_qt.py`, `batch_drop_dialog_qt.py`) — sa logique complète tient dans une seule fonction interne, `_make_batch_library` (`drop_handler_qt.py:202-210`), qui se contente d'ouvrir la fenêtre Bibliothèque existante (skill `library`) avec son dialogue de création pré-rempli.

## Seul des 8 traitements accessible uniquement par drop, jamais par menu

`batch-processing` le documente déjà : contrairement aux 5 conversions + recompression + import métadonnées (chacune ayant une entrée dans le menu Fichier > Batch, `menubar_qt.py`), **la Bibliothèque n'a aucune entrée de menu équivalente** — `panel_widget.py` n'a pas de méthode `_batch_library` (grep `_batch_library` dans `panel_widget.py` : aucun résultat). Le seul point d'entrée est le drag & drop d'un ou plusieurs dossiers sur l'application, puis le choix "Créer une bibliothèque à partir du(des) dossier(s)" dans `BatchDropDialog`. Cohérent avec la nature de l'action : créer une bibliothèque à partir d'un dossier précis n'a pas vraiment de sens comme commande de menu générale (qui demanderait alors un dossier via `QFileDialog`, un pas suffisamment différent de "Nouvelle bibliothèque" déjà existant dans le menu Bibliothèque pour ne pas dupliquer une fonctionnalité).

## Aucun scan de fichiers, aucune confirmation, aucune progression propre

**Différence structurelle totale avec les 7 autres flux batch** : pas de `os.walk` pour compter des fichiers, pas de `_ConfirmDialog`/`_RecompressConfirmDialog`, pas de `_ProgressDialog`/`_ThreadSignals`/`threading.Thread`, pas de log, pas de fenêtre de résumé dédiée. `_make_batch_library` (`drop_handler_qt.py:202`) :

```python
def _make_batch_library():
    sorted_dirs = sorted(dirs, key=lambda d: _natural_sort_key(os.path.basename(d).lower()))
    master_dir  = sorted_dirs[0]
    extra_dirs  = sorted_dirs[1:]
    lib_win = open_library_window(parent)
    QTimer.singleShot(0, lambda: _open_new_db_with_preset(lib_win, master_dir, extra_dirs))
```

1. **Tri des dossiers déposés** par nom naturel (`_natural_sort_key`, skill `sort-images` — même fonction de tri que les autres flux batch, appliquée ici aux noms de dossiers plutôt qu'aux fichiers).
2. **Le premier dossier trié devient le "master directory"** — celui utilisé pour créer la base `.mvdb` elle-même ; les dossiers suivants (`extra_dirs`) sont de simples répertoires supplémentaires ajoutés à la même bibliothèque après coup, pas des bases séparées.
3. **Ouvre `LibraryWindow`** (`open_library_window`, skill `library` — singleton jamais détruit, réutilisé s'il existe déjà) puis, **au tick suivant** (`QTimer.singleShot(0, ...)`, nécessaire pour laisser `LibraryWindow` finir de s'afficher/s'initialiser avant d'empiler un second dialogue par-dessus), ouvre `NewDbDialog` pré-rempli.

## `NewDbDialog(parent=lib_win, preset_dir=master_dir)` — le vrai point d'intégration

Le dialogue de création de nouvelle bibliothèque (skill `library`, fenêtre standard "Nouvelle bibliothèque" qu'on obtient aussi via le menu de `LibraryWindow` en usage normal) accepte un paramètre `preset_dir` qui **pré-remplit le champ dossier maître** avec `master_dir` — c'est l'unique adaptation nécessaire côté `NewDbDialog` pour supporter ce flux batch ; le reste du formulaire (nom de la base, dossier de sauvegarde du `.mvdb`) reste à la charge de l'utilisateur comme pour une création normale. Pas de nouvelle classe de dialogue créée pour ce flux, contrairement aux 7 autres qui ont chacun leur `_ConfirmDialog`/`_ProgressDialog`/résumé dédiés.

## Dossiers supplémentaires — après validation, pas avant

`dlg.accepted.connect(lambda: lib_win._on_new_db_accepted(dlg, extra_dirs=extra_dirs))` — les `extra_dirs` ne sont utilisés **qu'après** que l'utilisateur ait validé `NewDbDialog` (bouton OK), pas injectés silencieusement dans le formulaire. `LibraryWindow._on_new_db_accepted` (`library_window.py:2629`) :

1. Crée la base (`LibraryDB.create(filepath, master_dir)`) avec **seulement** le dossier maître.
2. **Ajoute chaque dossier supplémentaire séparément** (`for d in (extra_dirs or []): self._db.add_directory(d)`) — même mécanisme que "Ajouter un dossier" en usage normal de la Bibliothèque (skill `library`), pas un traitement spécial pour ce cas batch.
3. Réinitialise l'état de la fenêtre (lignes affichées, colonnes visibles aux valeurs par défaut — "nouvelle DB, pas de config sauvegardée").
4. Lance `self._action_scan()` — le scan initial (skill `library`) indexe **tous** les dossiers (maître + supplémentaires) en une seule passe, pas un scan par dossier.

## Comment étendre

- **Ajouter une entrée de menu équivalente** (actuellement drop uniquement) : nécessiterait une méthode `PanelWidget._batch_library` sur le modèle des autres `_batch_convert_*` (`panel_widget.py`), qui ouvrirait `QFileDialog.getExistingDirectory` puis appellerait directement `open_library_window`/`NewDbDialog` avec `preset_dir` — pas de scan de fichiers à faire au préalable comme les autres flux, donc plus simple à câbler ; changement de comportement notable (nouvelle entrée de menu), à ne pas faire sans consigne explicite.
- **Changer quel dossier devient le "master"** (actuellement le premier après tri naturel) : uniquement `sorted_dirs[0]` dans `_make_batch_library` — un changement ici modifie aussi implicitement quel dossier sert de source pour le nom par défaut proposé dans `NewDbDialog` (à vérifier dans le code de `NewDbDialog` si ce comportement est touché).

## Pièges connus

- **Aucun scan de fichiers avant ouverture des dialogues** — contrairement aux 7 autres flux qui affichent "aucun fichier trouvé" si le dossier est vide de contenu pertinent, ce flux ouvre `NewDbDialog` inconditionnellement dès qu'au moins un dossier a été déposé, même vide ; c'est le scan Bibliothèque lui-même (`_action_scan`, skill `library`) qui découvrira l'absence de contenu après coup, pas une vérification préalable.
- **Pas de fenêtre de résumé** — contrairement aux 7 autres flux qui affichent systématiquement un `_XxxSummaryDialog`/`_MetadataSummaryDialog` en fin de traitement, ce flux se termine simplement par l'affichage de la Bibliothèque avec son scan en cours (feedback visuel du scan lui-même, pas un résumé batch dédié).
- **`QTimer.singleShot(0, ...)` nécessaire entre l'ouverture de `LibraryWindow` et `NewDbDialog`** — supprimer ce délai pourrait empiler `NewDbDialog` avant que `LibraryWindow` ait fini de s'afficher, avec un risque de centrage/parentage incorrect (voir CLAUDE.md, piège "second dialogue déclenché par un premier qui se ferme aussitôt" pour un piège de nature similaire, bien que le mécanisme exact diffère ici).
- **`extra_dirs` ajoutés après coup via `add_directory`, pas au moment de la création de la base** — la fonction `LibraryDB.create` ne connaît que le dossier maître ; ne pas chercher un paramètre multi-dossiers dans `create()` lui-même.

## Références croisées

- `batch-processing` — architecture commune des 8 traitements ; identifie déjà ce flux comme n'étant accessible que par drop, sans entrée de menu.
- `library` — `LibraryWindow`/`NewDbDialog`/`LibraryDB.create`/`add_directory`/`_action_scan`, tous réutilisés tels quels par ce flux ; `NewDbDialog` accepte `preset_dir` spécifiquement pour ce cas d'usage.
- `sort-images` — `_natural_sort_key`, réutilisé ici pour trier les dossiers déposés plutôt que des noms de fichiers.
