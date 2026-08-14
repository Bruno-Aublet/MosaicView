---
name: qt-tooltips
description: Ajouter, modifier ou déboguer un tooltip Qt (survol vignette/canvas, widget de QDialog, cellule de QTableWidget) dans MosaicView. Utiliser dès qu'une tâche implique un tooltip, hoverMoveEvent/hoverEnterEvent/hoverLeaveEvent, ou setToolTip.
---

# Tooltips Qt — MosaicView

**Jamais `QToolTip` ni `widget.setToolTip(...)` natif Qt** (flashe ou disparaît aux micro-mouvements de souris sur un `QGraphicsView`, et ne respecte de toute façon ni le thème ni la police courante). Un seul mécanisme réutilisable dans tout le projet : `OverlayTooltip` (`modules/qt/overlay_tooltip_qt.py`) — un `QLabel` overlay repositionné près du curseur, stylé selon le thème, avec sa police mise à jour dynamiquement.

Avant d'écrire le moindre code de tooltip, chercher si une instance `OverlayTooltip` existe déjà sur la fenêtre/canvas courant (`self._overlay_tip`, `self.viewport()._overlay_tip`, etc.) et la réutiliser. Ne jamais en créer une deuxième sur le même widget parent.

## Trois façons d'utiliser `OverlayTooltip` selon le contexte

### 1. Canvas/vignettes (`QGraphicsView`, survol d'un item de scène)

`MosaicCanvas` (`mosaic_canvas.py`) expose deux méthodes qui délèguent à son `self._overlay_tip` :
```python
canvas.show_item_tooltip(html)   # affiche/replace le contenu
canvas.hide_item_tooltip()       # cache
```
À appeler depuis les hover events de l'item de scène (pas du canvas lui-même) :
```python
def hoverMoveEvent(self, event):
    c = self._canvas()
    if c:
        text = self._get_tooltip_text()
        c.show_item_tooltip(self._format_tooltip(text) if text else "")
    super().hoverMoveEvent(event)

def hoverEnterEvent(self, event):
    ...  # même logique que hoverMoveEvent

def hoverLeaveEvent(self, event):
    c = self._canvas()
    if c:
        c.hide_item_tooltip()
    super().hoverLeaveEvent(event)
```
Le canvas cache aussi le tooltip dans son propre `leaveEvent` (souris qui quitte tout le viewport, pas juste un item) :
```python
def leaveEvent(self, event):
    self.hide_item_tooltip()
    super().leaveEvent(event)
```
Voir `mosaic_canvas.py:396-414` (hover events d'un item) et `mosaic_canvas.py:1087-1093,1738-1740` (méthodes du canvas).

### 2. Widget quelconque d'un QDialog (QCheckBox, QLineEdit, header de tableau...)

Suivi automatique Enter/MouseMove/Leave, sans toucher aux événements du widget :
```python
self._overlay_tip.track(widget, html)       # installe le suivi, un texte par widget
self._overlay_tip.set_tracked_html(html, widget)  # met à jour le texte après coup (ex. retranslate())
self._overlay_tip.untrack(widget)            # retire le suivi
```
Exemple réel (`resize_dialog_qt.py:516-524`) :
```python
def _tip_html():
    text = _("dialogs.reduce_size.multi_page_width_tooltip")
    escaped = _html.escape(text).replace("\n", "<br>")
    return (
        f'<table style="max-width:360px;white-space:normal;">'
        f'<tr><td>{escaped}</td></tr>'
        f'</table>'
    )
self._overlay_tip.track(self._multi_page_cb, _tip_html())
```
Si le texte peut changer (langue, valeur dynamique), rappeler `set_tracked_html(...)` dans `_retranslate()` — même règle que tout texte affiché (voir CLAUDE.md, retraduction à la volée).

**Piège — `set_tracked_html()` ne rafraîchit pas un tooltip déjà affiché à l'écran** : il ne fait que remplacer le texte stocké pour le widget ; le nouveau contenu n'apparaît qu'au prochain `Enter`/`MouseMove` détecté sur ce widget. Si le texte peut changer **sans que la souris bouge** — typiquement une icône bi-mode dont le tooltip dépend d'un état basculé par clic droit pendant que le curseur reste immobile dessus (ex. `_ViewerToolbar._update_straighten_tooltip`/`_update_sharpness_tooltip`, `viewer_toolbar_qt.py`, skill `viewers`) — l'ancien texte reste visible tant que l'utilisateur ne bouge pas la souris, ce qui est trompeur juste après le clic. Corrigé (v1.7.3+) par `force_refresh_visible(widget)` : réaffiche immédiatement le tooltip avec son texte à jour si et seulement s'il est déjà visible sur ce widget précis (no-op sinon). À appeler juste après `set_tracked_html(...)` dans tout handler de bascule d'état déclenché par un clic (pas un survol) :
```python
self._overlay_tip.set_tracked_html(tip, self._buttons["sharpness"])
self._overlay_tip.force_refresh_visible(self._buttons["sharpness"])
```

### 3. Cellules tronquées d'un `QTableWidget`

`_CellTooltipFilter` (même fichier) : n'affiche l'overlay que si le texte de la cellule dépasse la largeur de colonne. S'installe sur le viewport de la table, pas sur la table elle-même — voir les usages existants pour le pattern d'installation exact avant d'en ajouter un nouveau.

## Format HTML du contenu

Toujours `<table style="max-width:360px;white-space:normal;"><tr><td>texte</td></tr></table>`, jamais `<p>` : un bloc `<p>` affiché dans ce `QLabel` ajoute une ligne vide parasite en bas (Qt applique un `margin-bottom` aux blocs `<p>` dans ce contexte, pas aux `<table><td>`). `MAX_WIDTH = 340` est déjà appliqué par la classe (`self._label.setMaximumWidth(...)`) — le `max-width:360px` dans le HTML est une marge de sécurité pour le wrap interne, pas une largeur à recalculer.

## Instanciation

Une instance par fenêtre/canvas, créée une fois :
```python
self._overlay_tip = OverlayTooltip(parent_widget)  # viewport() pour un QGraphicsView, le widget lui-même sinon
```
Penser à appeler `update_font()` quand la police change et `_apply_style()`/`apply_theme()` quand le thème change (déjà fait pour le canvas dans `_apply_theme_bg()` — reproduire le même câblage pour toute nouvelle instance).

## Dette existante — ne pas reproduire

Les `setToolTip()` morts suivants ont été retirés (v1.5.6+) — ne pas les recréer :
- Items du sous-menu "Bases récentes" (`library_window.py`, `menubar_qt.py`) : un `setToolTip()` sur un `QAction` de `QMenu` ne s'affiche jamais dans l'usage normal d'un menu (le highlight au survol prend toute la place, pas de délai de hover).
- Boutons undo/redo du créateur d'icônes (`ico_creator_qt.py::_make_icon_btn`) : le paramètre `tooltip` du helper n'était jamais renseigné par aucun de ses 4 appels, donc `setToolTip("")` ne pouvait rien afficher. Le paramètre a été retiré.

Si un vrai tooltip est un jour voulu sur ces boutons/items, utiliser `OverlayTooltip` (méthode 2 ci-dessus), pas `setToolTip()`.
