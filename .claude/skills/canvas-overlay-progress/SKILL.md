---
name: canvas-overlay-progress
description: Localiser ou modifier le label rouge de progression en surimpression ("Chargement en cours..."). Mécanisme central obligatoire pour TOUT indicateur de ce type, jamais réinventer. Utiliser dès qu'une tâche touche à canvas_overlay_qt.py ou show_canvas_text/hide_canvas_text.
---

# Overlay de progression (label rouge) — MosaicView

**Le** mécanisme du projet pour afficher un texte rouge en surimpression pendant un traitement long ("Chargement en cours... 45%", "Rotation en cours... 80%", "Redimensionnement en cours...", etc.). Mécanisme central et transversal, réutilisé par au moins 11 fichiers à travers le projet — **toujours réutiliser ce mécanisme pour un nouvel indicateur de progression de ce type, ne jamais en recréer un similaire à la main** (même règle de principe que `OverlayTooltip` pour les tooltips, skill `qt-tooltips`).

## Fichier unique — `modules/qt/canvas_overlay_qt.py`

Très compact (65 lignes), deux fonctions publiques seulement :

- **`show_canvas_text(canvas, text, item_holder)`** — crée ou met à jour le label rouge centré.
- **`hide_canvas_text(canvas, item_holder)`** — le cache et le détruit.

## `show_canvas_text(canvas, text, item_holder)`

- **`canvas`** : n'importe quel widget Qt possédant `.rect()` — pas seulement `MosaicCanvas` (la mosaïque principale). Fonctionne aussi sur le canvas de la Bibliothèque (`library_window.py`, un `QTableWidget`) et sur d'autres widgets. Le label est créé comme **enfant du widget passé** (`QLabel(canvas)`), donc positionné en coordonnées locales à ce widget.
- **`text`** : chaîne déjà **traduite et formatée** par l'appelant (`_("labels.loading", percent=pct)`, etc.) — cette fonction ne fait aucune traduction elle-même, uniquement de l'affichage. Peut contenir des `\n` explicites pour un texte multi-lignes (`setWordWrap(True)` est déjà actif) — voir `pdf_loading_qt.py` pour un exemple à 2-3 lignes.
- **`item_holder`** : **liste à un seul élément** (`[None]` au départ, ou `[]`), pas un simple attribut d'instance — c'est le mécanisme de persistance du label entre deux appels successifs (voir section dédiée plus bas, c'est le point le plus facile à mal comprendre en copiant ce pattern).

Comportement : si `item_holder[0]` est `None` ou n'est pas encore un `QLabel`, en crée un nouveau (style rouge fixe `rgb(220, 0, 0)`, fond transparent, `WA_TransparentForMouseEvents` — **le label ne bloque jamais les clics/survols sous lui**, centré, `WordWrap` actif) et le range dans `item_holder[0]`. Sinon, réutilise le label existant. Dans tous les cas : police recalculée à chaque appel (`get_current_font(24, bold=True)`, suit un éventuel changement de police en cours de traitement), texte mis à jour, largeur fixée à celle du widget (`vr.width()`), hauteur ajustée au contenu (`adjustSize()`), **centré verticalement** dans le widget (`(vr.height() - lbl.height()) // 2`, jamais centré horizontalement au sens strict — la largeur du label égale celle du widget avec `AlignCenter`, donc le texte est centré horizontalement par l'alignement du contenu, pas par la position du label), puis `show()` + `raise_()` (toujours au premier plan).

## `hide_canvas_text(canvas, item_holder)`

Cache (`hide()`) puis planifie la destruction (`deleteLater()`) du label, remet `item_holder[0] = None`. Le paramètre `canvas` n'est en réalité pas utilisé dans le corps de la fonction (le label se retrouve via `item_holder[0]` uniquement) — gardé dans la signature par symétrie avec `show_canvas_text` et pour un futur usage éventuel, pas une erreur d'appel si `canvas` semble redondant en lisant le code.

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

## Bouton "Annuler" associé — `_show_cancel_item` (`web_import_qt.py:106`)

Compagnon quasi systématique de `show_canvas_text` pour tout traitement annulable (pas défini dans `canvas_overlay_qt.py` lui-même, mais dans `web_import_qt.py`, réimporté depuis les autres fichiers qui en ont besoin — voir `rotate-flip`/`page-resize` pour des exemples d'import) :

- Même principe d'`item_holder` séparé (un second `[None]`, ex. `self._cancel_item_holder`), même style de construction paresseuse.
- Couleur différente (`rgb(255, 102, 102)`, rouge plus clair) avec `text-decoration: underline` et `Qt.PointingHandCursor` — visuellement distinct du label de progression, cliquable.
- **`anchor_lbl`** : positionné juste sous le label de progression passé en référence (`anchor_lbl.y() + anchor_lbl.height()`), pas à une position fixe indépendante — le bouton Annuler suit donc le label de progression si son texte change de hauteur (ex. passage d'une à deux lignes). Sans `anchor_lbl`, se positionne à une position par défaut à peu près centrée (`(vr.height() - lbl.height()) // 2 + 40`).
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

Grep `show_canvas_text\(` pour la liste exhaustive à jour plutôt que de supposer que cette liste reste figée, mais au moment de la rédaction de ce skill :

- **Chargement d'archive/image** (`archive_loader.py`, `panel_widget.py`, `pdf_loading_qt.py`) — `labels.loading`, texte multi-lignes pour le PDF (nom de fichier + page en cours + pourcentage).
- **Conversion de format** (`conversion_dialogs_qt.py`) — `labels.converting`.
- **Fusion/import d'archive dans une session ouverte** (`import_merge_qt.py`) — `labels.import_progress`.
- **Rotation/miroir** (`image_transforms_qt.py`, skill `rotate-flip`) — `label_key` paramétrable (`labels.rotating`/`labels.flipping`), voir ce skill pour le détail de l'intégration avec le worker QThread.
- **Redimensionnement** (`resize_dialog_qt.py`, skill `page-resize`) — `labels.resizing`.
- **Import web** (`web_import_qt.py`) — texte de téléchargement + variante "annulation en cours..." pendant le clic sur Annuler lui-même.
- **Impression** (`printing_qt.py`) — `labels.print_preparing`.
- **Bibliothèque** (`library_window.py`) — plusieurs usages sur le `QTableWidget` de résultats (peuplement de table, opérations longues), la seule utilisation confirmée sur un widget autre que le canvas mosaïque/PDF.

## Style visuel — volontairement non paramétrable

Couleur (`rgb(220, 0, 0)`), taille de police (`24`, gras), centrage vertical : **codés en dur dans `show_canvas_text`**, pas de paramètre pour les personnaliser par appelant. C'est délibéré — voir la règle générale du CLAUDE.md sur la cohérence visuelle attendue entre les indicateurs similaires du projet (même esprit que le modèle de boutons imposé pour les dialogues de confirmation). Un besoin de style différent pour un cas particulier doit être discuté avant d'ajouter un paramètre optionnel à cette fonction, plutôt que de la dupliquer ou de la contourner.

**Exception notée dans le CLAUDE.md, section "Détails de style annexes"** : le rouge est réservé aux vrais messages d'erreur/statut — un avertissement non bloquant devrait normalement préférer la couleur de texte du thème en gras+italique. Ce mécanisme-ci **déroge** à cette préférence pour tout texte de progression (rouge systématique, pas un avertissement au sens de cette règle), cohérent avec son rôle : signaler visuellement qu'un traitement est en cours et qu'il ne faut pas interagir avec la zone concernée.

## Comment étendre

- **Ajouter un nouveau point d'usage** (nouvelle opération longue nécessitant un indicateur) : créer un `item_holder = [None]` (local ou attribut d'instance selon la durée de vie nécessaire), appeler `show_canvas_text(widget, texte_traduit, item_holder)` à chaque tick de progression, `hide_canvas_text(widget, item_holder)` en fin de traitement (succès, erreur, **et** annulation — les trois chemins doivent cacher l'overlay, voir piège ci-dessous). Ajouter un bouton Annuler avec `_show_cancel_item` si l'opération est interruptible.
- **Changer le style visuel global** (couleur, taille, position) : uniquement dans `show_canvas_text`, un seul point de vérité pour tout le projet — se propage automatiquement à tous les appelants.
- **Support d'un widget qui n'a pas `.rect()`** (cas hypothétique) : la fonction suppose `canvas.rect()` disponible ; tout widget standard Qt (`QWidget` et descendants) l'a nativement, donc peu de raison que ce soit un problème en pratique.

## Pièges connus

- **Ne jamais recréer un `item_holder` à chaque appel** — casse la persistance du label, empile des `QLabel` fantômes plutôt que de mettre à jour le texte (voir section dédiée).
- **`hide_canvas_text` doit être appelée sur les 3 chemins de sortie** (succès, erreur, annulation) — un chemin d'erreur qui ne cache pas l'overlay laisse le label rouge affiché indéfiniment, masquant la zone sous-jacente même après la fin réelle du traitement.
- **`text` doit déjà être traduit** avant l'appel — `show_canvas_text` ne traduit rien elle-même, un texte non traduit passé par erreur s'affiche tel quel (clé brute ou anglais en dur selon la source).
- **Le style n'est pas paramétrable** — ne pas ajouter de logique de couleur/taille conditionnelle sans discussion préalable, ce mécanisme est délibérément uniforme dans tout le projet.
- **`canvas` dans `hide_canvas_text` n'est pas réellement utilisé** — ne pas se fier à sa valeur pour un comportement conditionnel, le ciblage se fait entièrement via `item_holder`.
- **`_show_cancel_item` vit dans `web_import_qt.py`, pas dans `canvas_overlay_qt.py`** — import à faire explicitement depuis ce fichier si un bouton Annuler est nécessaire, ne pas chercher cette fonction dans le fichier de l'overlay lui-même.
- **Chevauchement visuel avec le message d'accueil du canvas** (`mosaic_canvas.py::_show_empty_message`, "Déposez ici...") : les deux textes se centrent verticalement au même endroit quand le canvas mosaïque est vide (aucun comic ouvert), donc un overlay de progression lancé sur un canvas vide se superpose visuellement au message gris de fond. Corrigé (2026-08-14, import web) via l'attribut dynamique `canvas._loading` déjà utilisé ailleurs (`panel_widget.py`/`scan_dialog_qt.py`) : le mettre à `True` **et** vider explicitement `canvas._empty_items` de la scène juste avant d'afficher l'overlay (`_loading=True` empêche seulement la *recréation* future du message par `render_mosaic()`, pas la suppression de celui déjà présent), puis le remettre à `False` **sur tous les chemins de sortie** (succès, erreur, annulation) — voir `_suppress_empty_hint()`/`_restore_empty_hint()` dans `web_import_qt.py` pour un exemple réutilisable de ce pattern. Un traitement qui peut démarrer sur un canvas vide devrait suivre ce même pattern plutôt que de découvrir le chevauchement au test.

## Références croisées

- `rotate-flip` — exemple complet d'intégration avec un worker `QThread` par lot, overlay de progression + bouton Annuler + rollback à l'annulation.
- `page-resize` — même pattern d'intégration worker, avec en plus la gestion des dimensions aberrantes (`OutlierDialog`) pendant que l'overlay peut être affiché.
- `archive-image-loading` — chargement d'archive/image, premier et plus visible usage de ce mécanisme (`labels.loading`).
- `web-import` — téléchargement d'images depuis le web, avec la variante "annulation en cours..." pendant le clic sur Annuler.
- `library` — seul usage confirmé sur un widget autre que le canvas mosaïque (le `QTableWidget` de la fenêtre Bibliothèque).
- `qt-tooltips` — mécanisme unique similaire en esprit (`OverlayTooltip`, à réutiliser systématiquement plutôt que réinventer), pour les tooltips plutôt que les indicateurs de progression.
- `add-translation` — clés `labels.loading`/`labels.converting`/`labels.resizing`/`labels.rotating`/`labels.flipping`/`labels.import_progress`/`labels.print_preparing`/`web.web_download_cancel`, toutes à traduire dans les ~47 langues du projet comme n'importe quelle autre clé.
