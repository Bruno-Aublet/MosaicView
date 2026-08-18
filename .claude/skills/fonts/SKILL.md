---
name: fonts
description: Localiser ou modifier la gestion des polices de MosaicView (police par défaut, polices pIqaD/Tengwar des langues fictives, offset de taille global). Utiliser dès qu'une tâche touche à font_manager_qt.py, get_current_font, ou au rendu klingon/sindarin/quenya CSUR.
---

# Polices — MosaicView

Une seule fonction (`get_current_font()`) que **tout** le projet doit appeler pour obtenir une police — jamais de `QFont(...)` construit à la main dans un widget. Elle choisit automatiquement entre la police système normale et l'une des deux polices spéciales chargées au démarrage (pIqaD pour le klingon, Tengwar pour le sindarin/quenya), selon la langue d'interface active.

## Fichier actif — `modules/qt/font_manager_qt.py`

Le seul fichier réellement utilisé. Contient :
- **`FontManagerQt`** — classe qui charge les fichiers `.ttf` via `QFontDatabase` et retient le nom de famille réel obtenu.
- **`init_font_manager()`** / **`get_font_manager()`** — instance globale (`_font_manager`, module-level), initialisée une seule fois dans `MainWindow.__init__` (`MosaicView.py:122`).
- **`get_current_font(size=10, family="Arial", bold=False)`** — la fonction à utiliser partout, résout la police selon la langue active + l'offset de taille configuré.

### `modules/qt/font_loader.py` — utilitaires seulement, pas de chargement de police

Malgré son nom, ce fichier **ne charge aucune police**. Il ne contient plus que :
- **`resource_path(relative_path)`** — résolution de chemins compatible PyInstaller (`sys._MEIPASS`), largement importée par de nombreux modules du projet, pas spécifique aux polices.
- **`PIQAD_FONT_FILE`** / **`TENGWAR_FONT_FILES`** — noms des fichiers `.ttf` embarqués, utilisés par `user_guide_qt.py` (boutons "Exporter la police pIqaD/Tengwar" du mode d'emploi, simple `shutil.copy2` — voir skill `user-guide`).

Il contenait historiquement sa propre version homonyme de `init_font_manager()` (installation de police au niveau système Windows via `AddFontResourceEx`/`ctypes`), du code mort de l'ère pré-Qt jamais appelé — **supprimé le 2026-07-16**. Le chargement de police réel est exclusivement dans `font_manager_qt.py`.

## `get_current_font()` — la fonction à utiliser partout

```python
def get_current_font(size: int = 10, family: str = "Arial", bold: bool = False) -> QFont:
```

Logique de résolution, dans l'ordre :
1. Lit la langue d'interface courante (`loc.get_current_language()`, voir skill `add-translation`).
2. Lit l'offset de taille de police global (`config.get_font_size_offset()`) et l'ajoute à `size` (`adjusted = max(1, size + offset)`, jamais en dessous de 1pt).
3. Si la langue courante est `'tlh-piqad'` **et** que la police pIqaD a bien été chargée → `QFont(fm.piqad_font_name, adjusted)`.
4. Si la langue courante est `'sjn-tengwar'` ou `'qya-tengwar'` **et** que la police Tengwar a bien été chargée → `QFont(fm.tengwar_font_name, adjusted)`.
5. Sinon → `QFont(family, adjusted)` — `family` par défaut `"Arial"`, mais le paramètre existe pour les rares cas où un appelant a besoin d'une police spécifique (peu utilisé en pratique, quasi tous les appels du projet utilisent la valeur par défaut).
6. `bold=True` applique `f.setBold(True)` après coup, indépendamment du choix de police.

**Import standard dans tout le projet** : `from modules.qt.font_manager_qt import get_current_font as _get_current_font` (alias quasi systématique) — grep `_get_current_font\(` pour trouver n'importe quel appel existant comme modèle.

## Les deux polices spéciales et leur rôle

| Police | Fichier embarqué | Fallbacks système (si le fichier est absent) | Langues qui l'activent |
|---|---|---|---|
| **pIqaD** | `fonts/pIqaD-qolqoS.ttf` | `pIqaD qolqoS`, `KApIqaD`, `Code2000`, `Constructium` | `tlh-piqad` (klingon en écriture pIqaD) |
| **Tengwar** | `fonts/AlcarinTengwarVF.ttf` | `Alcarin Tengwar`, `Tengwar Annatar`, `Tengwar Telcontar`, `Tengwar Formal` | `sjn-tengwar` (sindarin), `qya-tengwar` (quenya) |

- **Chargement** (`FontManagerQt.load_fonts()`, appelé une fois par `init_font_manager()`) : `QFontDatabase.addApplicationFont(chemin)` sur le fichier embarqué dans `fonts/` (résolu via `resource_path()` — compatible PyInstaller, le dossier `fonts/` est embarqué dans l'exécutable packagé). Si le chargement réussit, le vrai nom de famille est lu via `QFontDatabase.applicationFontFamilies(fid)` — **jamais supposé être le nom du fichier**, une police `.ttf` peut déclarer un nom de famille différent de son nom de fichier.
- **Fallback système** : si le fichier embarqué est absent/corrompu, cherche parmi `QFontDatabase.families()` (polices déjà installées sur la machine) une des familles de secours listées — permet de dégrader proprement plutôt que d'échouer si l'utilisateur a par exemple installé KApIqaD indépendamment.
- **Fallback ultime** : si ni le fichier ni aucun fallback système n'est trouvé, `piqad_font_name`/`tengwar_font_name` restent `None` — `get_current_font()` retombe alors sur `family` (Arial) même si la langue active est une langue CSUR, **le texte reste lisible mais dans la mauvaise police** (pas de crash, dégradation silencieuse).

## Rapport avec les langues — les 6 langues fictives concernées

Codes de langue reconnus par le projet (voir `MosaicView.py:125-126`, `_fictional`/`_fictional_order`) :

```python
_fictional_order = ['tlh', 'tlh-piqad', 'sjn', 'sjn-tengwar', 'qya', 'qya-tengwar']
```

**Chaque langue fictive existe en 2 variantes** : la variante "de base" (`tlh`, `sjn`, `qya` — texte translittéré en alphabet latin, lisible avec une police normale) et sa **variante CSUR** correspondante (`tlh-piqad`, `sjn-tengwar`, `qya-tengwar` — même contenu textuel mais rendu dans l'écriture native fictive du Conlang Script Unicode Registry). **Seules les 3 variantes CSUR déclenchent une police spéciale** dans `get_current_font()` — les variantes de base (`tlh`/`sjn`/`qya`) utilisent la police système normale comme n'importe quelle autre langue. Voir skill `add-translation` pour le contenu des traductions elles-mêmes (fichiers `locales/*.json`, glossaires de référence par langue) — ce skill-ci ne couvre que le rendu visuel de ces langues, pas leur contenu textuel.

### Construction de la liste des langues avec leur police associée

`MosaicView.py:124-138` construit `self._language_list` (utilisé pour peupler le combo langue, voir section icon-toolbar plus bas) sous forme `[(code, nom_affiché, nom_police|None), ...]` :
- Les langues réelles (tout sauf les 6 fictives) reçoivent `font_name = None` — le combo ne leur associe aucune police spéciale.
- Les 6 langues fictives sont ajoutées **dans un ordre fixe explicite** (`_fictional_order`), à la suite des langues réelles — pas triées alphabétiquement comme les autres, pour les regrouper visuellement en fin de liste.
- `tlh-piqad` reçoit `font_name = fm.piqad_font_name` ; `sjn-tengwar`/`qya-tengwar` reçoivent `fm.tengwar_font_name` ; `tlh`/`sjn`/`qya` (variantes de base) reçoivent `font_name = None` comme les langues réelles.

## Offset de taille de police — global, pas par langue ni par panneau

`state.py:113-114` :
```python
MIN_FONT_SIZE_OFFSET = -5
MAX_FONT_SIZE_OFFSET = 10
```

- Persisté dans la config (`ConfigManager.get_/set_font_size_offset()`, clé `font_size_offset`, défaut `0`) — **un seul réglage pour toute l'application**, pas dédoublé par panneau contrairement à beaucoup d'autres réglages du projet (icon-toolbar, zip-compression, renumbering...) — voir skill `panels` pour le contraste avec ces réglages dédoublés.
- Modifié via `MainWindow._decrease_font_size()`/`_increase_font_size()` (`MosaicView.py:376-396`) : ajuste `cfg.set_font_size_offset(...)`, borné par `MIN_FONT_SIZE_OFFSET`/`MAX_FONT_SIZE_OFFSET`, puis **itère sur tous les panneaux** (`for p in self._all_panels(): p._reload_ui_fonts(); p._retranslate_banner()`) — voir skill `panels`, un changement de taille de police s'applique donc à panel1 **et** panel2 simultanément, jamais un seul.
- **Restauration au démarrage** : pas un pas explicite de `session_restore_qt.py` (voir skill `session-restore`) — l'offset est simplement lu par `get_current_font()` à chaque construction de widget, au fil du démarrage normal de l'UI, sans étape de restauration dédiée.
- **Reset aux valeurs par défaut** (`reset_to_defaults()`, voir skill `session-restore`) remet explicitement l'offset à 0 et appelle `_reload_ui_fonts()` sur tous les panneaux.

## `_reload_ui_fonts()` — ce qui doit être rafraîchi après un changement de police

`PanelWidget._reload_ui_fonts()` (`panel_widget.py:864`) — **point d'entrée unique** à appeler après tout changement affectant `get_current_font()` (taille ou langue) :

```python
def _reload_ui_fonts(self):
    build_menubar(self, self._build_menubar_callbacks(), self._menubar)
    self._canvas.render_mosaic()
    if hasattr(self._canvas, "_overlay_tip"):
        self._canvas._overlay_tip.update_font()
    if hasattr(self, "_icon_toolbar") and hasattr(self._icon_toolbar, "_overlay_tip"):
        self._icon_toolbar._overlay_tip.update_font()
    if hasattr(self, "_status_bar") and hasattr(self._status_bar, "_overlay_tip"):
        self._status_bar._overlay_tip.update_font()
    self._metadata_tab.apply_theme()
```

- **Menubar reconstruite entièrement** (`build_menubar`) — pas de simple `setFont()`, car un changement de police peut aussi changer la police du **stylesheet** du menu (voir skill `qt-context-menus`, section "QMenu/QMenuBar — police écrasée par le stylesheet global" : `setFont()` seul est insuffisant sur Windows, écrasé par le stylesheet global de l'app).
- **`render_mosaic()`** — la mosaïque affiche des noms de fichiers (voir skill `mosaic-thumbnails`) qui doivent suivre la police courante, y compris en langue CSUR.
- **Trois `OverlayTooltip`** explicitement rafraîchis (canvas, icon-toolbar, status-bar) via `update_font()` — voir skill `qt-tooltips`, chaque instance d'`OverlayTooltip` doit être notifiée individuellement, pas de mécanisme global de propagation.
- **`_metadata_tab.apply_theme()`** (pas `refresh()`) — voir skill `tabs`, section "deux niveaux de mise à jour" : un changement de police est traité comme un cas de restylage léger, pas une reconstruction complète des widgets de l'onglet métadonnées.
- **Ce que cette fonction NE fait PAS** : elle ne touche pas à l'icon-toolbar elle-même (au-delà de son tooltip) ni à la status-bar elle-même — ces widgets écoutent le changement de langue via leur propre mécanisme (`language_signal.changed`, voir skill `add-translation`) qui rappelle `get_current_font()` en interne à chaque `_retranslate()`/`refresh()`, donc suivent déjà la police sans passer par `_reload_ui_fonts`.

## Interaction avec la colonne d'icônes — combo langue avec police par item

Voir skill `icon-toolbar` pour le mécanisme général du footer (réglette vignettes + combo langue). Spécifique aux polices :

- **`LanguageComboWidget`** (`icon_toolbar_qt.py:957`) reçoit la liste `[(code, nom, police), ...]` construite dans `MosaicView.py` (voir section précédente) et assigne à **chaque item du combo** sa propre police via `Qt.FontRole` (`combo.setItemData(idx, QFont(font_name, 9), Qt.FontRole)`) — **seulement pour les items dont `font_name` correspond à une police spéciale déjà chargée** (`_special = {piqad_font_name, tengwar_font_name} - {None}`). Un item de langue réelle ou de variante fictive de base (`tlh`/`sjn`/`qya`) n'a pas de `Qt.FontRole` assigné, donc hérite de la police par défaut du combo.
- **`_LangComboDelegate`** (`QStyledItemDelegate`) — dessine chaque ligne de la liste déroulante avec la police stockée dans son `Qt.FontRole` (permet de voir "Klingon (pIqaD)" écrit directement en pIqaD dans la liste, pas juste en texte latin) ; met aussi en **gras** l'item correspondant à la langue actuellement active (`_LANG_IS_CURRENT_ROLE`).
- **Le texte replié du combo** (widget fermé, pas la liste déroulante) suit aussi la police de l'item courant — `_LangCombo` (`QComboBox` custom) redessine son propre texte avec cette police, pas seulement les lignes de la liste ouverte.
- Taille de police fixée en dur à `9` pour ce combo spécifique (`QFont(font_name, 9)`) — **n'utilise pas `get_current_font()`/l'offset global** pour ces polices d'item, une particularité à connaître si l'offset de taille doit un jour s'appliquer aussi à ce combo.

## Interaction avec la mosaïque et les onglets

- **Mosaïque** (voir skill `mosaic-thumbnails`) : noms de fichiers sous chaque vignette, rendus via `get_current_font()` — recalculé à chaque `render_mosaic()`, jamais mis en cache au-delà d'un rendu.
- **Onglets** (voir skill `tabs`) : l'onglet Métadonnées applique `_get_current_font(...)` à chaque label/valeur dans `_restyle()`, y compris le tableau Pages (`Qt.FontRole` réévalué à chaque `data()` de `_PagesTableModel` — voir skill `tabs`, section tableau Pages, pour la raison de ce choix : suivre un changement de police sans reconstruire tout le modèle).
- **Panneaux** (voir skill `panels`) : chaque `PanelWidget` a ses propres widgets à rafraîchir via `_reload_ui_fonts()`, mais l'offset de taille et le `FontManagerQt` sont **partagés** entre panel1 et panel2 (contrairement à la config dédoublée `Panel2Config` d'autres réglages) — pas de police différente possible entre les deux panneaux.

## Comment ajouter une nouvelle police spéciale (ex. une 4ᵉ langue fictive avec sa propre écriture)

1. Déposer le fichier `.ttf` dans `fonts/`.
2. Ajouter une constante de nom de fichier dans `font_manager_qt.py` (sur le modèle de `_PIQAD_FILE`/`_TENGWAR_FILES`) et une liste de fallbacks système.
3. Ajouter les attributs `xxx_font_name`/le chargement correspondant dans `FontManagerQt.load_fonts()` et une méthode `get_xxx_font()` si un accès direct est nécessaire (au-delà de `get_current_font()`).
4. Étendre la condition de `get_current_font()` (`elif current_lang == 'nouveau_code': ...`).
5. Ajouter le/les nouveaux codes de langue à `_fictional`/`_fictional_order` dans `MosaicView.py`, avec le bon `font_name` associé lors de la construction de `_language_list`.
6. Ajouter les fichiers de traduction (`locales/xxx.json`) — voir skill `add-translation`, y compris tout script de conversion CSUR similaire à `convert_piqad_csur.py`/`convert_tengwar_csur.py` si applicable.
7. Vérifier la licence de la police (voir les méthodes `_show_full_piqad_license`/`_show_full_tengwar_license` dans `panel_widget.py`/`MosaicView.py` comme modèle d'attribution à reproduire).

## Pièges connus

- **Ne jamais construire un `QFont(...)` à la main dans un nouveau widget** — toujours `get_current_font()`, sinon le texte reste figé en Arial même si l'utilisateur passe en klingon pIqaD ou change la taille globale (règle CLAUDE.md n°3).
- **`font_loader.py` ne charge aucune police malgré son nom** — il ne contient que `resource_path()` et les constantes de noms de fichiers `.ttf` ; le chargement réel est dans `font_manager_qt.py::init_font_manager()` (son ancienne homonyme morte dans `font_loader.py` a été supprimée le 2026-07-16).
- **L'offset de taille de police est global, pas par panneau** — contrairement à la plupart des autres réglages du projet ; un changement affecte panel1 et panel2 simultanément par construction de `_decrease_font_size`/`_increase_font_size` qui itèrent sur `_all_panels()`.
- **Retraduction dynamique et police doivent toujours aller de pair** — voir règle CLAUDE.md n°2, piège "retraduction dynamique" : un `widget.setText(_('clé'))` sans `widget.setFont(get_current_font(taille))` juste à côté garde l'ancienne police (latine) pour les langues CSUR → glyphes illisibles après un changement de langue vers klingon/sindarin/quenya CSUR.
- **Le combo langue utilise une taille de police fixe (`9`) indépendante de l'offset global** pour les items en police spéciale — ne pas supposer que ce combo suit automatiquement un changement de taille de police globale.
- **Si le fichier `.ttf` embarqué est absent, la dégradation est silencieuse** — pas d'erreur ni d'avertissement affiché à l'utilisateur, le texte en langue CSUR s'affiche simplement dans la mauvaise police (Arial) sans prévenir ; à garder en tête pour diagnostiquer un rapport de "police bizarre en klingon".
- **Le glyphe du guillemet droit `"` est mal calibré dans `AlcarinTengwarVF.ttf`** (bounding box hors de la plage normale des autres lettres, rendu démesurément grand à l'écran) et **absent de `pIqaD-qolqoS.ttf`** (bascule sur une police système de secours). Aucun caractère de guillemet alternatif testé (« », " ") n'est mieux pris en charge par ces deux polices. Le contenu traduit en tlh/sjn/qya n'utilise donc plus ce caractère dans ses variantes CSUR (`tlh-piqad`, `sjn-tengwar`, `qya-tengwar`) — voir skill `add-translation` pour le mécanisme qui l'empêche de réapparaître.
