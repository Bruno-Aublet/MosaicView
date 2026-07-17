---
name: recent-items
description: Localiser ou modifier les fichiers récents et bases de bibliothèque récentes de MosaicView. Utiliser dès qu'une tâche touche à recent_files.py, recent_dbs.py, ou aux sous-menus "Fichiers récents"/"Bases de données récentes".
---

# Fichiers récents et bases récentes — MosaicView

Deux listes indépendantes mais structurellement identiques : les **fichiers récemment ouverts** (`recent_files.py`, menu Fichier) et les **bases de bibliothèque `.mvdb` récemment ouvertes** (`recent_dbs.py`, menu Bibliothèque). Les deux sont de simples wrappers fins autour de `ConfigManager` (skill `config-storage`) — aucune des deux ne stocke d'état propre, tout vit dans `%APPDATA%\MosaicView\.mosaicview_config.json`.

## Les deux modules, en parallèle

| | `recent_files.py` | `recent_dbs.py` |
|---|---|---|
| Constante max | `MAX_RECENT_FILES = 10` | `MAX_RECENT_DBS = 10` |
| Lecture | `get_recent_files()` | `get_recent_dbs()` |
| Ajout | `add_to_recent_files(filepath)` | `add_to_recent_dbs(filepath)` |
| Suppression d'une entrée | `remove_from_recent_files(filepath)` | `remove_from_recent_dbs(filepath)` |
| Vidage complet | `clear_recent_files()` | `clear_recent_dbs()` |
| Nettoyage au démarrage | `init_recent_files()` | `init_recent_dbs()` |

Chaque fonction délègue à la méthode `ConfigManager` correspondante (`get_recent_files`/`add_recent_file`/`set_recent_files`/`clean_recent_files`, et l'équivalent `_db` pour les bases) — voir `config_manager.py:452` (`clean_recent_files`) et `config_manager.py:484` (`clean_recent_dbs`) pour la logique de nettoyage elle-même (filtre les chemins qui n'existent plus sur disque).

Toutes les fonctions de mutation (`add_to_recent_files`/`add_to_recent_dbs`/`remove_from_recent_files`/`clear_recent_files`) sont enveloppées dans un `try/except Exception: pass` — un échec silencieux ici (ex. fichier de config verrouillé) n'empêche jamais l'action principale (ouverture/fermeture de fichier) de continuer ; l'entrée récente sera simplement absente au prochain démarrage.

## Cycle de vie

1. **`init_recent_files()`/`init_recent_dbs()`** — appelées une seule fois au démarrage (`panel_widget.py:274-276`), avant toute autre interaction : filtrent silencieusement les chemins qui n'existent plus (fichier supprimé/déplacé depuis la dernière session) hors de la liste persistée.
2. **`add_to_recent_files(filepath)`** — appelée à chaque sauvegarde CBZ réussie (`file_operations_qt.py:1138,1328`, voir skill `save-export` — `save_selection_as_cbz` et `create_cbz_from_images`) et à l'ouverture d'un fichier existant (`panel_widget.py:1823`). `add_to_recent_dbs(filepath)` équivalent appelé à l'ouverture/création d'une base bibliothèque (`library_window.py:2675-2676`, voir skill `library`).
3. **`_main_window._sync_recent_menus()`** — appelée systématiquement juste après un ajout (`file_operations_qt.py`, `panel_widget.py`) pour rafraîchir immédiatement le sous-menu affiché, sans attendre une reconstruction complète de la barre de menu (bien que celle-ci se reconstruise de toute façon à chaque `aboutToShow`, voir skill `menu-bar` — cet appel explicite garantit la cohérence même si le menu était déjà ouvert au moment de l'ajout, ce qui ne se produit normalement jamais en pratique mais reste une garde defensive).

## Consommation dans les menus

- **Fichiers récents** — sous-menu dans `_populate_file_menu` (`menubar_qt.py:76-85`, voir skill `menu-bar`) : une entrée par fichier (`os.path.basename(fp)` comme libellé, chemin complet capturé dans le lambda), désactivé si la liste est vide (`enabled=bool(recent_files)`), séparateur puis "Effacer l'historique" (`callbacks.get("clear_recent_files")`).
- **Bases récentes** — sous-menu dans `_populate_library_menu` (`menubar_qt.py:389-414`) : structure similaire mais avec une nuance — chaque entrée est **désactivée individuellement** si le fichier `.mvdb` n'existe plus sur disque (`if not os.path.exists(fp): act.setEnabled(False)`), plutôt que d'être filtrée en amont comme le fait `init_recent_dbs()` au démarrage. Cette vérification `os.path.exists` a donc lieu **deux fois à des moments différents** : une fois au nettoyage de démarrage (retire l'entrée de la liste), une fois à chaque construction du menu (grise l'entrée sans la retirer) — voir "Pièges".

## Comment modifier

- **Changer le nombre maximum d'entrées conservées** : `MAX_RECENT_FILES`/`MAX_RECENT_DBS` (10 par défaut), constante en tête de chaque fichier — passée explicitement en paramètre à `add_recent_file`/`add_recent_db` du `ConfigManager`, donc modifiable indépendamment pour chaque liste.
- **Ajouter un nouveau type d'élément "récent"** (ex. bibliothèque de recherches sauvegardées) : suivre le pattern exact de ces deux fichiers (6 fonctions : `get_`/`add_to_`/`remove_from_`/`clear_`/`init_`, plus la constante `MAX_`) et ajouter les méthodes correspondantes côté `ConfigManager` (voir skill `config-storage`) — ne pas dupliquer la logique de nettoyage/filtrage, la déléguer entièrement au config manager comme le font les deux modules existants.
- **Changer le comportement d'une entrée dont le fichier n'existe plus** : deux points d'intervention distincts selon l'effet voulu — `init_recent_files`/`init_recent_dbs` (retire silencieusement de la liste persistée, au démarrage seulement) vs la vérification `os.path.exists` inline dans `_populate_library_menu` (grise sans retirer, à chaque affichage du menu Bibliothèque). Le menu Fichiers récents, lui, **ne fait aucune vérification d'existence à l'affichage** — une entrée dont le fichier a été supprimé pendant la session reste cliquable jusqu'au prochain redémarrage (voir "Pièges").

## Pièges connus

- **Asymétrie de robustesse entre les deux menus** — le sous-menu Bases récentes vérifie `os.path.exists` à chaque construction (grise les entrées mortes), mais le sous-menu Fichiers récents ne le fait **pas** : une entrée supprimée en cours de session reste active et cliquable dans le menu Fichier jusqu'au prochain démarrage de l'application (seul `init_recent_files()` au démarrage suivant la retirera). Ne pas supposer que les deux listes ont le même niveau de robustesse face à un fichier disparu.
- **`add_to_recent_files`/`add_to_recent_dbs` avalent toute exception silencieusement** — un échec d'écriture de la config (disque plein, fichier verrouillé par un autre process) ne remonte jamais d'erreur visible à l'utilisateur ; si un bug de persistance des fichiers récents est signalé, vérifier d'abord si `ConfigManager` lui-même écrit correctement (skill `config-storage`) avant de chercher dans ces deux fichiers, qui ne font qu'appeler ses méthodes sans logique propre.
- **`_sync_recent_menus()` doit exister sur `_main_window`** — les appels dans `file_operations_qt.py` sont protégés par `hasattr(parent, "_main_window") and hasattr(parent._main_window, "_sync_recent_menus")`, donc un contexte où `parent` n'a pas de `_main_window` (ex. un futur appel depuis un contexte batch/headless) sauterait silencieusement le rafraîchissement du menu sans erreur — l'entrée serait quand même persistée dans la config, juste pas reflétée immédiatement dans l'UI ouverte.

## Références croisées

- `config-storage` — `ConfigManager`, seule source réelle de persistance et de logique de nettoyage (`clean_recent_files`/`clean_recent_dbs`) ; ces deux fichiers ne sont que des façades.
- `menu-bar` — sous-menus "Fichiers récents" (menu Fichier) et "Bases de données récentes" (menu Bibliothèque), reconstruits à chaque `aboutToShow`.
- `save-export` — points d'appel de `add_to_recent_files` après une sauvegarde CBZ réussie.
- `library` — points d'appel de `add_to_recent_dbs` à l'ouverture/création d'une base `.mvdb`.
