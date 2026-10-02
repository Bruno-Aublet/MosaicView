---
name: zip-compression
description: Localiser ou modifier le choix du mode/niveau de compression ZIP (STORED vs DEFLATED) à l'écriture des CBZ. Utiliser dès qu'une tâche touche à zip_compression_level, zip_compression_kwargs, ou à l'indicateur ZIP de la statusbar.
---

# Compression ZIP des CBZ — MosaicView

**Ne pas confondre avec `adjust-compression`** : ce skill couvre la compression **ZIP** du fichier CBZ conteneur (STORED vs DEFLATED, `zip_compression_level`), un mécanisme totalement distinct de la compression **JPEG** d'une image individuelle (`compression_quality`, section "Compression" du panneau Ajustements d'image — voir skill `adjust-compression`). Les deux notions n'ont aucun rapport de code, seulement un nom de section proche.

## Vue d'ensemble

Le niveau de compression n'est **pas** un choix fait au cas par cas à chaque écriture : c'est un **réglage persisté par panneau** (0 à 9), lu à chaque fois qu'un CBZ est (ré)écrit. 0 = `ZIP_STORED` (pas de compression, écriture rapide, fichier plus gros). 1-9 = `ZIP_DEFLATED` avec ce `compresslevel` (plus lent, fichier plus petit).

Le flux complet :
1. L'utilisateur ouvre le réglage (menu ou clic droit sur l'indicateur ZIP de la statusbar) → `ZipCompressionDialog`.
2. Le dialogue lit/écrit le niveau via un objet config (`ConfigManager` panneau 1, ou `Panel2Config` panneau 2).
3. Chaque fonction qui écrit un CBZ relit ce niveau au moment de l'écriture et le convertit en kwargs `zipfile.ZipFile(...)` via `zip_compression_kwargs()`.

## Le convertisseur central : `zip_compression_kwargs()`

[modules/qt/utils.py](modules/qt/utils.py) :
```python
def zip_compression_kwargs(level: int) -> dict:
    if level <= 0:
        return {"compression": zipfile.ZIP_STORED}
    return {"compression": zipfile.ZIP_DEFLATED, "compresslevel": level}
```
**Toujours passer par cette fonction** pour ouvrir un `zipfile.ZipFile` en écriture dans ce projet — ne jamais recalculer `compression=`/`compresslevel=` à la main dans un nouveau call-site. Import local (`from modules.qt.utils import zip_compression_kwargs`), pattern systématique dans tout le code existant.

Deux call-sites (`_run_img_conversion` et `_run_imgs_to_single_cbz` dans `batch_dialogs_qt.py`) forcent `ZIP_STORED` en dur sans passer par `zip_compression_kwargs()` — ce sont des cas particuliers (contexte batch spécifique, le flux IMG→CBZ, voir skill `batch-img-convert` pour le rationnel), pas le pattern à suivre pour un nouveau code.

## Stockage du réglage — `ConfigManager` / `Panel2Config`

[modules/qt/config_manager.py](modules/qt/config_manager.py) (panneau 1 / config globale) :
```python
def get_zip_compression_level(self):
    return int(self.config.get('zip_compression_level', 0))

def set_zip_compression_level(self, level):
    return self.set('zip_compression_level', int(level))
```
Panneau 2 : clé séparée `zip_compression_level_panel2` (`get_/set_zip_compression_level_panel2`), exposée via le wrapper `Panel2Config` (même fichier) qui redirige `get/set_zip_compression_level()` vers la clé `_panel2` — **chaque panneau a son propre réglage indépendant**, ce n'est pas un réglage global partagé entre panel1/panel2 (contrairement aux marques-pages, voir skill `bookmarks`).

Pour savoir quel objet config utiliser depuis du code de panneau, reproduire `PanelWidget._zip_compression_config()` ([panel_widget.py](modules/qt/panel_widget.py)) :
```python
def _zip_compression_config(self):
    if self._is_primary:
        return get_config_manager()
    from modules.qt.config_manager import Panel2Config
    return Panel2Config(get_config_manager())
```

## Le dialogue de réglage — `ZipCompressionDialog`

[modules/qt/zip_compression_dialog_qt.py](modules/qt/zip_compression_dialog_qt.py) — non-modal (conforme règle CLAUDE.md n°4), `QSpinBox` range 0-9, thème/langue/police dynamiques, centré sur le panneau parent. Pattern identique à `split_dialog_qt.py` (mentionné en tête de fichier, voir skill `page-split` pour ce que ce dialogue fait réellement) — s'en inspirer pour tout dialogue de réglage similaire.

- Ouverture : `show_zip_compression_dialog(parent, config, on_result=None)` — `config` est le résultat de `_zip_compression_config()` ci-dessus, pas directement `ConfigManager`.
- À l'OK (`_on_ok`), appelle `self._config.set_zip_compression_level(...)` puis émet `result_signal(True)`.
- Textes : `dialogs.zip_compression.window_title` (`_wt`, obligatoire pour un titre de fenêtre), `.title`, `.explanation`, `.level_label` dans les fichiers `locales/*.json`.

## Trois points d'entrée pour ouvrir le dialogue

1. **Barre de menus, menu Archives** : `menu.zip_compression` dans `_populate_archives_menu` ([menubar_qt.py](modules/qt/menubar_qt.py)) → callback `open_zip_compression_dialog` câblé dans `build_menubar_callbacks` ([menubar_callbacks_qt.py](modules/qt/menubar_callbacks_qt.py)) vers `mw._open_zip_compression_dialog`.
2. **Menu contextuel du canvas** (clic droit sur zone vide de la mosaïque) : `menu.zip_compression` dans `show_canvas_context_menu` ([context_menus_qt.py](modules/qt/context_menus_qt.py)), à plat parmi les actions liées à l'archive (marque-pages, etc.) — ce menu n'a pas de sous-menu "Archives" dédié.
3. **Clic droit sur l'indicateur ZIP de la statusbar** : `StatusBar.set_zip_right_click_callback()` ([status_bar_qt.py](modules/qt/status_bar_qt.py)) → `PanelWidget._open_zip_compression_dialog()`, avec garde anti-double-ouverture (`existing.raise_()` si le dialogue est déjà ouvert) et rafraîchissement de la statusbar au résultat.

Les trois convergent vers la même méthode `PanelWidget._open_zip_compression_dialog()` — un seul endroit à modifier pour changer le comportement d'ouverture du dialogue, quel que soit le point d'entrée.

## L'indicateur ZIP de la statusbar

Pour le mécanisme générique de la barre de statut (layout, `refresh()`, `OverlayTooltip`, sizePolicy) → skill `status-bar`. Ici, uniquement ce qui est spécifique à ZIP.

Affiche l'état réel du fichier actuellement ouvert (pas le réglage) : `stored`, `deflated`, ou vide (non-CBZ / rien d'ouvert). Textes `labels.zip_indicator_stored` / `_deflated` / `_na`, tooltips `tooltip.zip_indicator_*`. Logique d'affichage : `StatusBar.refresh` ([status_bar_qt.py](modules/qt/status_bar_qt.py)).

**Clic gauche** sur l'indicateur (`PanelWidget._zip_indicator_clicked`) propose de réécrire le fichier selon l'état détecté vs le réglage par défaut :
- Rien d'ouvert → ne fait rien.
- Pas d'archive (mode images seules) → propose de créer un CBZ (`_create_cbz_from_images`).
- Fichier non-CBZ → propose de l'enregistrer en CBZ (`_apply_new_names`).
- CBZ déjà `stored` ET réglage par défaut = 0 → ne fait rien (déjà optimal, rien à gagner).
- CBZ compressé, ou `stored` avec réglage par défaut > 0 → propose de réenregistrer (confirmation via `ConfirmDialog`, clés `messages.questions.zip_recompress.*` / `zip_convert_to_cbz.*`).

**Clic droit** → ouvre `ZipCompressionDialog` (voir plus haut).

## Détection de l'état réel d'un CBZ — `_detect_zip_compression_state()`

[modules/qt/archive_loader.py](modules/qt/archive_loader.py) : lit le `compress_type` de la **première entrée fichier** (hors dossiers) d'un `.cbz` existant pour déterminer `'stored'` / `'deflated'` / `None`. Utilisé à l'ouverture d'archive (`ArchiveLoader._on_finished`, stocké dans `state.zip_compression_state`) et pour vérifier si une recompression batch est nécessaire (`_run_recompress` dans `batch_dialogs_qt.py`, skill `batch-recompress` pour le détail du critère de skip).

Ce n'est qu'une **heuristique sur la première entrée** — une archive avec des `compress_type` mixtes entre entrées n'est pas détectée correctement, mais ce cas ne se produit pas avec les CBZ écrits par MosaicView (un seul niveau appliqué à tout le zip via `zip_compression_kwargs()`).

`state.zip_compression_state` est remis à `None` à la fermeture de fichier (`force_close_file`, `file_close_qt.py`) et dans `PanelWidget._load_files`, puis recalculé en dur (`"stored" if comp_level <= 0 else "deflated"`) juste après chaque écriture réussie plutôt que relu depuis le disque — 5 endroits dans `file_operations_qt.py` (`save_as_cbz`, `create_cbz_from_images`, et 3 dans `_write_apply_new_names`).

## Tous les points d'écriture réels (où `zip_compression_kwargs()` est appliqué)

Les points d'écriture dans `file_operations_qt.py` sont détaillés côté flux de sauvegarde dans le skill `save-export` (les 6 méthodes historiques, chaîne de validation, dialogues associés) — ce skill-ci ne couvre que le réglage/la détection de compression elle-même.

Liste à revérifier par grep `zip_compression_kwargs(` avant de s'y fier :

| Fichier | Fonction(s) | Contexte |
|---|---|---|
| `file_operations_qt.py` | `_write_zip_with_progress` | écriture CBZ générique avec overlay de progression |
| `file_operations_qt.py` | `save_selection_as_cbz` | sauvegarde de la sélection |
| `file_operations_qt.py` | `_write_apply_new_names` (3 appels) | variantes d'enregistrement (fichier temporaire puis remplacement) |
| `batch_dialogs_qt.py` | `_run_cbr_conversion`, `_run_cb7_conversion`, `_run_cbt_conversion`, `_run_pdf_conversion`, `_run_recompress` | opérations batch (traitement multi-fichiers) |
| `batch_metadata_dialog_qt.py` | `_BatchMetadataOrchestrator._save_state_for_file` | assistant de métadonnées par lot |
| `library_window.py` | `_convert_file_to_cbz`, `_open_comicinfo_editor`, `_write_comicinfo_and_reindex` | opérations depuis la bibliothèque (fenêtre séparée, sa propre logique d'écriture) |

Pour ajouter un **nouveau** point d'écriture de CBZ : importer `zip_compression_kwargs` depuis `modules.qt.utils`, récupérer le niveau via `get_config_manager().get_zip_compression_level()` (ou l'objet config approprié si le code est dans le contexte d'un panneau précis — voir `_zip_compression_config()` plus haut), et passer `**zip_compression_kwargs(level)` à `zipfile.ZipFile(..., 'w', ...)`. Ne pas oublier de mettre à jour `state.zip_compression_state` après écriture si le code touche à l'état d'un panneau affiché.

## Pièges

- **Ne pas confondre réglage et état détecté.** `get_zip_compression_level()` = préférence utilisateur (ce qui sera appliqué à la *prochaine* écriture). `state.zip_compression_state` = ce qu'est *actuellement* le fichier ouvert sur disque. Les deux peuvent diverger (ex. fichier compressé ouvert alors que le réglage par défaut est à 0) — c'est justement ce qui déclenche la proposition de réenregistrement au clic sur l'indicateur.
- **Panel1 et Panel2 ont des réglages indépendants.** Ne jamais appeler `get_config_manager().get_zip_compression_level()` en dur depuis du code de panneau 2 — utiliser `_zip_compression_config()` ou `Panel2Config`, sinon le réglage du panneau 2 écrase silencieusement celui du panneau 1 (même bug de classe que pour la disposition de toolbar, le mode de renumérotation, etc.).
- **`level` doit rester un entier 0-9** — `zip_compression_kwargs` ne valide pas la plage ; c'est le `QSpinBox` (`setRange(0, 9)`) qui garantit la borne côté UI. Un niveau hors plage passé directement à `zipfile.ZipFile(compresslevel=...)` lèverait une erreur zlib.
