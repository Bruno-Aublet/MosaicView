---
name: menu-bar
description: Localiser ou modifier la barre de menus de MosaicView (Fichier/Édition/Images/Archives/ComicVine/Bibliothèque/Système/À propos). Utiliser dès qu'une tâche touche à menubar_qt.py, menubar_callbacks_qt.py, ou à une entrée de la barre de menu horizontale.
---

# Barre de menus — MosaicView

La `QMenuBar` horizontale en haut de chaque panneau : 8 menus (Fichier, Édition, Images, Archives, ComicVine, Bibliothèque, Système, À propos) plus 2 chevrons aux extrémités (colonne d'icônes à gauche, minimap à droite). Deux fichiers : `modules/qt/menubar_qt.py` (construction/peuplement des menus) et `modules/qt/menubar_callbacks_qt.py` (le dict de callbacks consommé par le premier).

Structure parallèle à la colonne d'icônes (skill `icon-toolbar`) : les deux consomment le **même genre** de dict de callbacks côté `MainWindow`, et beaucoup d'entrées apparaissent aux deux endroits (voir "Doublons intentionnels" plus bas) — mais **pas de config indépendante par panneau ici** contrairement à la colonne d'icônes : la barre de menu n'a ni layout personnalisable, ni activation par utilisateur, ni persistance de disposition.

## Reconstruction à chaque ouverture (`aboutToShow`)

Contrairement à la colonne d'icônes (widgets persistants dont l'état activé/désactivé est recalculé), **chaque menu est entièrement vidé et repeuplé** (`menu.clear()` puis reconstruction complète) à chaque fois qu'il est sur le point de s'ouvrir, via le signal `aboutToShow` connecté dans `build_menubar()` :

```python
def make_handler(m, fn):
    def handler():
        fn(m, callbacks)
    return handler
menu.aboutToShow.connect(make_handler(menu, populate_fn))
```

Ce choix (plutôt que de garder les actions vivantes et juste basculer `setEnabled`) garantit que l'état du menu (grisé/actif, libellés dynamiques comme "Créer un CBZ" vs "Enregistrer le CBZ") est **toujours à jour au moment précis où l'utilisateur clique sur le menu**, sans synchronisation explicite à maintenir ailleurs dans le code. Le coût : chaque `_populate_*_menu` relit `_state_module.state` en direct et reconstruit tout, à chaque ouverture — acceptable ici car un menu ne s'ouvre que sur action utilisateur explicite (pas dans une boucle de rendu).

## Les 8 fonctions `_populate_*_menu(menu, callbacks)`

| Fonction | Menu | Contenu notable |
|---|---|---|
| `_populate_file_menu` | Fichier | Ouvrir, fichiers récents (sous-menu), fermer, import web, NFO, conversion en lot (sous-menu, actif seulement canvas vide), les 6 méthodes de sauvegarde (voir skill `save-export`), impression (voir skill `printing`), Quitter |
| `_populate_edit_menu` | Édition | Undo/Redo (Ctrl+Z/Ctrl+Y), copier/couper/coller/copier-archive (skill `clipboard`), supprimer/inverser/tout sélectionner/désélectionner, rafraîchir (F5), doublons (skill `duplicate-detection`) |
| `_populate_images_menu` | Images | Sous-menu Rotation (skill `rotate-flip`), redimensionner (`page-resize`), ajustements (`adjustments-panel`), redressement/clonage/texte (`viewers`), conversion de format (`image-format-conversion`), crop/join/split (`page-crop`/`page-merge`/`page-split`), GIF animé (`animated-gif`), créer .ico (`create-ico`), remplacer/supprimer image corrompue (`corrupted-images`) |
| `_populate_archives_menu` | Archives | Renumérotation (`renumbering`), aplatir l'arborescence (fonction locale, voir skill `flatten-directories` si créé), tri (`sort-images`), marque-pages (`bookmarks`) |
| `_populate_metadata_menu` | ComicVine | Récupérer métadonnées (`comicvine-metadata-fetch`), import en lot (`batch-metadata-import`), créer/modifier ComicInfo.xml (`comicinfo-metadata-editor`), clé API, liens externes |
| `_populate_library_menu` | Bibliothèque | Ouvrir la fenêtre, bases récentes (sous-menu), sous-menu Base de données dynamique — voir "Piège menu Bibliothèque" ci-dessous (skill `library`) |
| `_populate_system_menu` | Système | Sous-menu Langues (`_populate_language_menu`, voir ci-dessous), taille des vignettes, thème (`dark-mode`), taille de police, compression ZIP (`zip-compression`), mode d'emploi (`user-guide`), plein écran (F11), reset config, split-view (`panels`) |
| `_populate_about_menu` | À propos | Site web/GitHub/MAJ, changelog, don, mail (adresse jamais en dur, voir `get_support_email()` ci-dessous), config (effacer/ouvrir dossier), temp (`temp-files`), export polices pIqaD/Tengwar (`fonts`), licences (sous-menu) |

**Adresse mail de contact** : `open_mail` (dans `build_menubar_callbacks`, `menubar_callbacks_qt.py`) ouvre un lien `mailto:` construit via `get_support_email()` (`modules/qt/utils.py`), jamais une adresse en dur — la fonction reconstruit `mosaicview1969@gmail.com` à partir de morceaux séparés pour ne pas apparaître en clair dans le repo public (cible de scraping). Même fonction réutilisée par `icon-toolbar` et `scan`.

`_populate_language_menu` (appelée par `_populate_system_menu`, pas montée en menu de premier niveau elle-même) reproduit la logique des langues fictives : sépare "Langues réelles"/"Langues fictives" (en-têtes non cliquables, `setEnabled(False)`), coche (✓) la langue courante, applique la police pIqaD/Tengwar par item (`_LangComboDelegate`-like, voir skill `fonts`) pour les 3 langues CSUR concernées.

## `_add_action` / `_add_submenu` — helpers communs

Toute action doit passer par `_add_action(menu, label, callback, shortcut, enabled)` — applique systématiquement `get_current_font(9)` (règle CLAUDE.md n°3) et ne connecte le callback **que si `enabled and callback`** (une action désactivée ne déclenche jamais son callback même si un événement Qt fuité l'atteignait). `_add_submenu` applique la police au sous-menu via une combinaison `setFont` + stylesheet (`font-family`/`font-size` en CSS) — **les deux sont nécessaires**, un stylesheet Qt peut écraser `setFont` seul sur un `QMenu` (voir skill `qt-context-menus`, section QMenu/QMenuBar, pour ce piège documenté en détail).

## Les 2 chevrons + le bouton d'extension natif

- **Chevron gauche** (`sidebar_action`, premier élément ajouté à la `QMenuBar`) — rabat/déploie la colonne d'icônes (`toggle_toolbar`), texte `«`/`»` selon l'état, exposé via `mb._update_sidebar_chevron` (callable, pas juste connecté à `triggered`, pour permettre une mise à jour externe sans redéclencher l'action).
- **Chevron droit** (`minimap_action`, ajouté après les 8 menus) — affiche/masque la minimap (skill `minimap`), même pattern `«`/`»` (notez le sens **inversé** par rapport au chevron gauche : `»` si minimap visible, `«` si masquée — cohérent avec l'idée "la flèche pointe vers ce qui va apparaître/disparaître").
- **`_MenuBarExtButtonRepositioner`** — Qt affiche un bouton `…` natif (`qt_menubar_ext_button`) quand la barre est trop étroite pour tous les menus, mais le replace systématiquement au bord droit à chaque `updateGeometries()` — ce qui le ferait chevaucher visuellement les chevrons ci-dessus. Cette classe `QObject` installe un `eventFilter` qui **recorrige la position immédiatement après chaque déplacement imposé par Qt**, en recalculant où finit le dernier menu visible. Pas de récursion malgré le `move()` correctif dans le filtre : le nouveau `move()` redéclenche bien le filtre, mais recalcule la même position cible et ne bouge plus une seconde fois. Installé **une seule fois** par `QMenuBar` (`mb._ext_btn_repositioner`, vérifié avant réinstallation) car `build_menubar` est rejouée à chaque changement de langue sur la même barre.

## Point d'entrée — `build_menubar(window, callbacks, menubar=None)`

Vide entièrement la `QMenuBar` (`mb.clear()`) puis reconstruit les 8 menus + les 2 chevrons + le bouton d'extension. Rejouée intégralement à chaque changement de langue (pas de mécanisme de retraduction incrémentale comme les dialogues — voir règle CLAUDE.md n°2, ici la "retraduction à la volée" se fait en reconstruisant tout plutôt qu'en retraduisant chaque libellé en place).

## `build_menubar_callbacks(mw)` — `menubar_callbacks_qt.py`

Construit le dict complet de callbacks à partir de l'instance `MainWindow` (`mw`), organisé par section commentée (`# ── Fichier ──`, etc., même découpage que les 8 menus). Reçoit `mw` en paramètre plutôt que d'importer `MainWindow` directement — évite toute importation circulaire entre ce module et `panel_widget.py`.

## Comment modifier

- **Ajouter une entrée à un menu existant** : ajouter un `_add_action(menu, _("clé"), callbacks.get("nom_callback"), ...)` dans la fonction `_populate_*_menu` correspondante, puis ajouter `"nom_callback": ...` dans `build_menubar_callbacks` (`menubar_callbacks_qt.py`) — les deux fichiers doivent être touchés, jamais un seul.
- **Ajouter un nouveau menu de premier niveau** : ajouter un tuple `(_("menu.xxx"), _populate_xxx_menu)` à la liste `menus` dans `build_menubar()`, et écrire la fonction `_populate_xxx_menu(menu, callbacks)` suivant le pattern des 8 existantes (`menu.clear()` en première ligne, obligatoire).
- **Changer une condition d'activation** (grisé/actif) : dans la fonction `_populate_*_menu` concernée, au niveau du calcul des booléens en tête de fonction (`has_images`, `has_sel`, `can_save_cbz`...) — ces booléens sont **recalculés à chaque ouverture**, pas mis en cache, donc toujours cohérents avec l'état courant sans action supplémentaire.
- **Ajouter une entrée à la fois dans la barre de menu et la colonne d'icônes** : les deux fichiers sont indépendants (pas de source commune de définition) — voir skill `icon-toolbar` pour `ICON_DEFINITIONS`/`_ACTIVATION_RULES`, et dupliquer la condition d'activation manuellement si elle doit être identique aux deux endroits (aucune garantie automatique de cohérence entre les deux).

## Pièges connus

- **Aucune configuration par panneau** — contrairement à la colonne d'icônes (`Panel2Config`, voir skill `panels`), la barre de menu est structurellement identique sur panel1 et panel2 ; il n'existe pas de mécanisme pour masquer/réordonner une entrée différemment selon le panneau.
- **Duplication des conditions d'activation entre menu et colonne d'icônes** — ex. `has_img_sel`/`single_sel`/`multi_sel` dans `_populate_images_menu` sont recalculés indépendamment de `_ACTIVATION_RULES` dans `icon_toolbar_qt.py` ; une correction de logique d'activation dans l'un ne se propage jamais automatiquement à l'autre (même risque que documenté dans le skill `viewers` pour les 5 visionneuses).
- **Le menu Bibliothèque a un comportement conditionnel selon que la fenêtre existe déjà** — `_populate_library_menu` construit le sous-menu Base de données différemment si `_library_window` (singleton) est `None` ou non : s'il est `None`, les entrées `db_close`/`db_rename`/etc. sont ajoutées **désactivées en dur** (pas de logique d'état réelle) car il n'y a alors aucune fenêtre dont interroger l'état ; ne pas supposer que ce sous-menu reflète toujours l'état réel d'une base ouverte sans vérifier lequel des deux chemins s'est exécuté.
- **Le bouton d'extension natif Qt (`qt_menubar_ext_button`) est retrouvé par nom d'objet interne** (`findChild(QToolButton, "qt_menubar_ext_button")`) — ce nom est un détail d'implémentation Qt non documenté publiquement ; une évolution future de Qt pourrait le renommer, auquel cas `_MenuBarExtButtonRepositioner` ne serait simplement jamais installé (pas de crash, juste un bouton `…` non repositionné qui pourrait chevaucher les chevrons).
- **`_populate_language_menu` n'est jamais montée directement en menu de premier niveau** — elle peuple un sous-menu à l'intérieur de `_populate_system_menu` ; chercher "où s'affiche le sous-menu Langues" doit partir de `_populate_system_menu`, pas de la liste `menus` de `build_menubar`.

## Références croisées

- `icon-toolbar` — structure de callbacks parallèle, configuration par panneau que la barre de menu n'a pas ; nombreuses entrées dupliquées entre les deux (rotation, conversion, redimensionnement...).
- `qt-context-menus` — le piège `setFont` écrasé par un stylesheet sur `QMenu`, documenté en détail là-bas, appliqué ici via `_add_submenu`.
- `dark-mode` / `fonts` — thème et police appliqués à chaque `QMenu`/`QAction` via stylesheet + `setFont` combinés.
- `panels` / `minimap` — les 2 chevrons de la barre de menu pilotent respectivement la colonne d'icônes et la minimap, chacun avec sa configuration indépendante par panneau (gérée ailleurs, pas dans ce fichier).
- `library` — sous-menu Bibliothèque, `_library_window` singleton et `build_db_menu`.
- `save-export` / `printing` — la quasi-totalité du menu Fichier.
- `renumbering` / `sort-images` / `bookmarks` — le menu Archives dans son intégralité.
