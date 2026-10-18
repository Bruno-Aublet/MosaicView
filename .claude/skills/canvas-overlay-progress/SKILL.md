---
name: canvas-overlay-progress
description: Localiser ou modifier le label rouge de progression en surimpression ("Chargement en cours..."). Mécanisme central obligatoire pour TOUT indicateur de ce type, jamais réinventer. Utiliser dès qu'une tâche touche à canvas_overlay_qt.py ou show_canvas_text/hide_canvas_text.
---

# Overlay de progression (label rouge) — MosaicView

**Le** mécanisme du projet pour afficher un texte rouge en surimpression pendant un traitement long ("Chargement en cours... 45%", "Rotation en cours... 80%", "Redimensionnement en cours...", etc.). Mécanisme central et transversal, réutilisé par au moins 11 fichiers à travers le projet — **toujours réutiliser ce mécanisme pour un nouvel indicateur de progression de ce type, ne jamais en recréer un similaire à la main** (même règle de principe que `OverlayTooltip` pour les tooltips, skill `qt-tooltips`).

## Fichier unique — `modules/qt/canvas_overlay_qt.py`

Trois fonctions publiques :

- **`show_canvas_text(canvas, text, item_holder)`** — crée ou met à jour le label rouge.
- **`hide_canvas_text(canvas, item_holder)`** — le cache et le détruit (texte de progression comme lien Annuler).
- **`attach_overlay_label(canvas, lbl, anchor=None)`** — inscrit un label annexe (le lien Annuler de `_show_cancel_item`) juste sous le texte `anchor`.

## Empilement — plusieurs opérations en même temps sur un même widget

Deux imports web, une numérisation et un chargement d'images, une sauvegarde et un import… peuvent afficher leur texte en même temps sur le même widget. Chaque widget tient la liste de ses textes de progression, dans l'ordre d'apparition (attribut dynamique `canvas._mv_overlay_labels`, `_overlay_list`) ; chaque texte peut porter un label annexe (`lbl._mv_annex`, son lien Annuler). `_layout_overlays(canvas)`, appelée par les trois fonctions publiques, empile les textes les uns sous les autres, chacun suivi de son annexe visible, espacés de `_OVERLAY_GAP`, et centre l'ensemble verticalement ; les labels déjà détruits sont retirés de la liste au passage (`shiboken6.isValid`). **Piège** : chaque texte centré seul s'écrivait par-dessus les autres. Un nouvel indicateur de progression n'a rien à faire de plus que d'appeler ces fonctions : jamais de `move()` à la main sur un de ces labels (il serait déplacé au prochain réempilement). L'empilement n'est recalculé qu'à l'affichage, la mise à jour ou le retrait d'un texte, pas au redimensionnement du widget (comme le centrage d'origine).

## `show_canvas_text(canvas, text, item_holder)`

- **`canvas`** : n'importe quel widget Qt possédant `.rect()` — pas seulement `MosaicCanvas` (la mosaïque principale). Fonctionne aussi sur le canvas de la Bibliothèque (`library_window.py`, un `QTableWidget`) et sur d'autres widgets. Le label est créé comme **enfant du widget passé** (`QLabel(canvas)`), donc positionné en coordonnées locales à ce widget.
- **`text`** : chaîne déjà **traduite et formatée** par l'appelant (`_("labels.loading", percent=pct)`, etc.) — cette fonction ne fait aucune traduction elle-même, uniquement de l'affichage. Peut contenir des `\n` explicites pour un texte multi-lignes (`setWordWrap(True)` est déjà actif) — voir `pdf_loading_qt.py` pour un exemple à 2-3 lignes.
- **`item_holder`** : **liste à un seul élément** (`[None]` au départ, ou `[]`), pas un simple attribut d'instance — c'est le mécanisme de persistance du label entre deux appels successifs (voir section dédiée plus bas, c'est le point le plus facile à mal comprendre en copiant ce pattern).

Comportement : si `item_holder[0]` est `None` ou n'est pas encore un `QLabel`, en crée un nouveau (style rouge fixe `rgb(220, 0, 0)`, fond transparent, `WA_TransparentForMouseEvents` — **le label ne bloque jamais les clics/survols sous lui**, centré, `WordWrap` actif) et le range dans `item_holder[0]`. Sinon, réutilise le label existant. Dans tous les cas : police recalculée à chaque appel (`get_current_font(24, bold=True)`, suit un éventuel changement de police en cours de traitement), texte mis à jour, largeur fixée à celle du widget (`vr.width()`), hauteur ajustée au contenu (`adjustSize()`), `show()` + `raise_()` (toujours au premier plan), puis **placé par l'empilement** (section précédente : centré verticalement s'il est seul ; le texte est centré horizontalement par l'alignement du contenu, la largeur du label égalant celle du widget). Un nouveau label est ajouté en fin de pile ; un label existant garde sa place.

## `hide_canvas_text(canvas, item_holder)`

Retire le label de la pile du widget, le cache (`hide()`), planifie sa destruction (`deleteLater()`), remet `item_holder[0] = None`, puis réempile les textes restants. **`canvas` doit être le widget sur lequel le texte a été affiché** : c'est sa pile qui est réempilée.

## Pourquoi un `item_holder` (liste), pas un attribut direct

**Le point le plus important à comprendre avant de réutiliser ce mécanisme.** Le pattern `item_holder = [None]` existe pour permettre à `show_canvas_text`/`hide_canvas_text` de **muter une référence partagée** entre l'appelant et la fonction, sans que l'appelant ait besoin de définir une classe ou de gérer un attribut `self._xxx_label` lui-même :

```python
item_holder = [None]          # créé une fois, avant le traitement
_show_canvas_text(canvas, "...", item_holder)   # item_holder[0] devient le QLabel
_show_canvas_text(canvas, "... 50%", item_holder)  # réutilise le même label, juste le texte change
_hide_canvas_text(canvas, item_holder)             # item_holder[0] redevient None
```

Une liste Python est mutable et passée par référence — `item_holder[0] = lbl` à l'intérieur de la fonction est visible immédiatement par l'appelant qui détient la même liste, sans avoir besoin de retourner et réassigner quoi que ce soit. C'est ce qui permet d'utiliser ce mécanisme aussi bien dans une fonction procédurale simple (`resize_dialog_qt.py`, `item_holder` local) que dans une classe `QThread`/`QDialog` (`self._loading_item_holder = [None]` en attribut d'instance, réutilisé à travers plusieurs méthodes/callbacks).

**Piège si on copie ce pattern sans le comprendre** : créer un nouvel `item_holder = [None]` à chaque appel de `show_canvas_text` plutôt que de réutiliser le même objet liste casse la persistance — un nouveau `QLabel` serait créé et empilé par-dessus l'ancien à chaque mise à jour de pourcentage, au lieu de mettre à jour le texte du label existant.

## Bouton "Annuler" associé — `_show_cancel_item` (`web_import_qt.py`)

Compagnon quasi systématique de `show_canvas_text` pour tout traitement annulable (pas défini dans `canvas_overlay_qt.py` lui-même, mais dans `web_import_qt.py`, réimporté depuis les autres fichiers qui en ont besoin — voir `rotate-flip`/`page-resize` pour des exemples d'import) :

- Même principe d'`item_holder` séparé (un second `[None]`, ex. `self._cancel_item_holder`), même style de construction paresseuse.
- Couleur différente (`rgb(255, 102, 102)`, rouge plus clair) avec `text-decoration: underline` et `Qt.PointingHandCursor` — visuellement distinct du label de progression, cliquable.
- **`anchor_lbl`** : le texte de progression auquel le lien appartient. `_show_cancel_item` le passe à `attach_overlay_label(canvas, lbl, anchor_lbl)` : le lien devient l'annexe de ce texte et l'empilement le place juste dessous, y compris quand le texte change de hauteur (passage d'une à deux lignes) ou quand un autre texte apparaît ou disparaît au-dessus. Sans `anchor_lbl` (ou si ce texte n'est plus affiché), le lien est empilé comme un texte à part entière.
- `on_click` : callback appelé au clic gauche (`mousePressEvent` réassigné directement sur le `QLabel`, pas un vrai signal Qt — approche minimaliste cohérente avec la simplicité du reste de ce mécanisme).

Usage typique complet (voir `rotate-flip`/`page-resize`/`web-import` pour des exemples réels) :
```python
_show_canvas_text(canvas, _("labels.rotating", percent=pct), item_holder)
_show_cancel_item(canvas, f"[ {_('buttons.cancel')} ]", cancel_holder, on_cancel_fn, anchor_lbl=item_holder[0])
# ... traitement ...
_hide_canvas_text(canvas, item_holder)
_hide_canvas_text(canvas, cancel_holder)   # hide_canvas_text fonctionne aussi pour le label Annuler, même fonction générique
```

## Inventaire des usages réels dans le projet

Grep `show_canvas_text\(` pour revérifier la liste avant de s'y fier :

- **Chargement d'archive/image** (`archive_loader.py`, `panel_widget.py`, `pdf_loading_qt.py`) — `labels.loading`, texte multi-lignes pour le PDF (nom de fichier + page en cours + pourcentage).
- **Conversion de format** (`conversion_dialogs_qt.py`) — `labels.converting`.
- **Fusion/import d'archive dans une session ouverte** (`import_merge_qt.py`) — `labels.import_progress`.
- **Rotation/miroir** (`image_transforms_qt.py`, skill `rotate-flip`) — `label_key` paramétrable (`labels.rotating`/`labels.flipping`), voir ce skill pour le détail de l'intégration avec le worker QThread.
- **Redimensionnement** (`resize_dialog_qt.py`, skill `page-resize`) — `labels.resizing`.
- **Jointure d'images animées** (`merge_dialog_qt.py`, `MergeDialog._start_animated_join`, skill `page-merge`) — `labels.joining`, avec bouton Annuler, worker QThread.
- **Import web** (`web_import_qt.py`) — texte de téléchargement + variante "annulation en cours..." pendant le clic sur Annuler lui-même.
- **Impression** (`printing_qt.py`) — `labels.print_preparing`.
- **Sauvegarde** (`file_operations_qt.py`, `_SavingOverlay`, skill `save-export`) — `labels.saving_cbz`, enveloppe de `show_canvas_text`/`hide_canvas_text` (méthodes `update`/`remove`) ; s'empile avec un import ou une numérisation en cours.
- **Numérisation** (`scan_dialog_qt.py`), **redressement automatique** (`deskew_qt.py`), **macros** (`macro_engine.py`, `macro_tool_qt.py`, dans la visionneuse).
- **Bibliothèque** (`library_window.py`) — plusieurs usages sur le `QTableWidget` de résultats (peuplement de table, opérations longues), la seule utilisation confirmée sur un widget autre que le canvas mosaïque/PDF.
- **BD vide** (`mosaic_canvas.py`, `MosaicCanvas._update_empty_comic_label`, holder `_empty_comic_holder`) — `labels.empty_comic`, seul texte de ce mécanisme qui n'accompagne pas un traitement : affiché tant qu'un fichier est ouvert sans plus aucun fichier dedans (`AppState.document_is_empty`), à la place du message d'accueil. Recalculé par `render_mosaic` (contenu, langue, police), par `_relayout` (largeur du texte au redimensionnement) et par `hold_empty_hint`/`release_empty_hint` ; masqué pendant un chargement (jeton tenu) et pendant l'ouverture d'une archive ou d'un PDF (`canvas._replacing_load_check`, fourni par `PanelWidget`), qui pose `current_file` avant d'avoir la moindre page.

## Style visuel — volontairement non paramétrable

Couleur (`rgb(220, 0, 0)`), taille de police (`24`, gras), centrage vertical : **codés en dur dans `show_canvas_text`**, pas de paramètre pour les personnaliser par appelant. C'est délibéré — voir la règle générale du CLAUDE.md sur la cohérence visuelle attendue entre les indicateurs similaires du projet (même esprit que le modèle de boutons imposé pour les dialogues de confirmation). Un besoin de style différent pour un cas particulier doit être discuté avant d'ajouter un paramètre optionnel à cette fonction, plutôt que de la dupliquer ou de la contourner.

**Exception notée dans le CLAUDE.md, section "Détails de style annexes"** : le rouge est réservé aux vrais messages d'erreur/statut — un avertissement non bloquant devrait normalement préférer la couleur de texte du thème en gras+italique. Ce mécanisme-ci **déroge** à cette préférence pour tout texte de progression (rouge systématique, pas un avertissement au sens de cette règle), cohérent avec son rôle : signaler visuellement qu'un traitement est en cours et qu'il ne faut pas interagir avec la zone concernée. Le texte « BD vide » suit la même règle, à la demande de l'utilisateur : un panneau vide dont le comic est encore ouvert doit se voir.

## Comment étendre

- **Ajouter un nouveau point d'usage** (nouvelle opération longue nécessitant un indicateur) : créer un `item_holder = [None]` (local ou attribut d'instance selon la durée de vie nécessaire), appeler `show_canvas_text(widget, texte_traduit, item_holder)` à chaque tick de progression, `hide_canvas_text(widget, item_holder)` en fin de traitement (succès, erreur, **et** annulation — les trois chemins doivent cacher l'overlay, voir piège ci-dessous). Ajouter un bouton Annuler avec `_show_cancel_item` si l'opération est interruptible.
- **Changer le style visuel global** (couleur, taille, position) : uniquement dans `show_canvas_text`, un seul point de vérité pour tout le projet — se propage automatiquement à tous les appelants.
- **Support d'un widget qui n'a pas `.rect()`** (cas hypothétique) : la fonction suppose `canvas.rect()` disponible ; tout widget standard Qt (`QWidget` et descendants) l'a nativement, donc peu de raison que ce soit un problème en pratique.

## Pièges connus

- **Ne jamais recréer un `item_holder` à chaque appel** — casse la persistance du label, empile des `QLabel` fantômes plutôt que de mettre à jour le texte (voir section dédiée).
- **`hide_canvas_text` doit être appelée sur les 3 chemins de sortie** (succès, erreur, annulation) — un chemin d'erreur qui ne cache pas l'overlay laisse le label rouge affiché indéfiniment, masquant la zone sous-jacente même après la fin réelle du traitement.
- **`text` doit déjà être traduit** avant l'appel — `show_canvas_text` ne traduit rien elle-même, un texte non traduit passé par erreur s'affiche tel quel (clé brute ou anglais en dur selon la source).
- **Le style n'est pas paramétrable** — ne pas ajouter de logique de couleur/taille conditionnelle sans discussion préalable, ce mécanisme est délibérément uniforme dans tout le projet.
- **`canvas` dans `hide_canvas_text` doit être le widget d'affichage** — c'est la pile de ce widget qui est mise à jour ; un autre widget laisserait le texte retiré compté dans l'empilement jusqu'à sa destruction.
- **Ne jamais dessiner un texte de progression comme item de la scène de la mosaïque** (`QGraphicsTextItem`) : toute reconstruction de la mosaïque (entrée dans un sous-dossier, taille des vignettes…) le détruit, et il échappe à l'empilement. Les labels de ce mécanisme sont des widgets posés sur le canvas, que la reconstruction ne touche pas.
- **`_show_cancel_item` vit dans `web_import_qt.py`, pas dans `canvas_overlay_qt.py`** — import à faire explicitement depuis ce fichier si un bouton Annuler est nécessaire, ne pas chercher cette fonction dans le fichier de l'overlay lui-même.
- **Chevauchement visuel avec le message d'accueil du canvas** (`mosaic_canvas.py::_show_empty_message`, "Déposez ici...") : les deux textes se centrent verticalement au même endroit quand le canvas mosaïque est vide (aucun comic ouvert), donc un overlay de progression lancé sur un canvas vide se superpose visuellement au message gris de fond. Fix : `MosaicCanvas.hold_empty_hint(token)` **et** vider explicitement `canvas._empty_items` de la scène juste avant d'afficher l'overlay (le jeton empêche seulement la *recréation* future du message par `render_mosaic()`, pas la suppression de celui déjà présent), puis `release_empty_hint(token)` **sur tous les chemins de sortie** (succès, erreur, annulation). `canvas._loading` est une propriété en lecture seule, vraie tant qu'un jeton est tenu. **Un jeton par chargement** (`"image_load"` dans `panel_widget.py`, le `ScanController` lui-même, `("web", numéro du dépôt)` dans `web_import_qt.py`, un `object()` propre à chaque fusion dans `import_merge_qt.import_and_merge_archive` et `pdf_loading_qt.import_and_merge_pdf`, posé à l'affichage du texte de progression et retiré avec lui) : plusieurs chargements peuvent tourner ensemble, et le premier terminé ne doit pas faire réapparaître le message sous les autres — jamais un simple booléen. Le même jeton masque le texte « BD vide » (une fusion dans une BD vide l'afficherait sinon au-dessus de sa progression). Voir `_suppress_empty_hint()`/`_restore_empty_hint()` dans `web_import_qt.py` pour un exemple réutilisable. Un traitement qui peut démarrer sur un canvas vide, ou sur une BD vide, devrait suivre ce même pattern plutôt que de découvrir le chevauchement au test.

## Références croisées

- `rotate-flip` — exemple complet d'intégration avec un worker `QThread` par lot, overlay de progression + bouton Annuler + rollback à l'annulation.
- `page-resize` — même pattern d'intégration worker, avec en plus la gestion des dimensions aberrantes (`OutlierDialog`) pendant que l'overlay peut être affiché.
- `archive-image-loading` — chargement d'archive/image, premier et plus visible usage de ce mécanisme (`labels.loading`).
- `web-import` — téléchargement d'images depuis le web, avec la variante "annulation en cours..." pendant le clic sur Annuler.
- `library` — seul usage confirmé sur un widget autre que le canvas mosaïque (le `QTableWidget` de la fenêtre Bibliothèque).
- `qt-tooltips` — mécanisme unique similaire en esprit (`OverlayTooltip`, à réutiliser systématiquement plutôt que réinventer), pour les tooltips plutôt que les indicateurs de progression.
- `add-translation` — clés `labels.loading`/`labels.converting`/`labels.resizing`/`labels.rotating`/`labels.flipping`/`labels.import_progress`/`labels.print_preparing`/`web.web_download_cancel`, toutes à traduire dans les ~47 langues du projet comme n'importe quelle autre clé.
