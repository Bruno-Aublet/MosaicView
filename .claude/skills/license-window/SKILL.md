---
name: license-window
description: Localiser ou modifier la fenêtre de licence de MosaicView (résumé cliqué depuis le copyright, accès aux licences complètes GPL/UnRAR/7-Zip/pIqaD/Tengwar). Utiliser dès qu'une tâche touche à license_dialog_qt.py ou au copyright "© Bruno Aublet 2025-2026" cliquable.
---

# Fenêtre de licence — MosaicView

Le texte `© Bruno Aublet 2025-2026` cliquable, présent en 3 endroits de l'interface (footer de la colonne d'icônes, menu contextuel, barre de menu), ouvre un résumé de licence (`_LicenseDialog`) ; depuis ce résumé, un bouton "Voir la licence complète" ouvre le texte intégral de la GPL (`_FullLicenseDialog`). Un menu séparé ("Licences", dans À propos) donne un accès **direct** aux 5 licences complètes (GPL + 4 licences tierces embarquées) sans passer par le résumé. Un seul fichier : `modules/qt/license_dialog_qt.py`.

## Les deux fenêtres

### `_LicenseDialog` — résumé de licence (`show_license_dialog_qt(parent)`)

Fenêtre non-modale, `QTextBrowser` + un bouton "Voir la licence complète". Le contenu HTML est généré dynamiquement par `_populate()` à partir d'une **seule clé de traduction** (`labels.license_text`, texte brut multi-paragraphes séparés par des lignes vides) :

- Détecte les titres de section par heuristique (`is_heading` : ligne tout en majuscules, non vide, ne commençant pas par `http`, longueur > 2) — pas de balisage explicite dans la traduction elle-même, juste une convention de casse.
- Ajoute des espacements verticaux (`&nbsp;` dans un `<p>` vide) avant chaque titre, avec un espacement double avant le tout premier titre détecté.
- Détecte et transforme en lien cliquable toute URL au format `<https://...>` (chevrons obligatoires, `url_pattern = r'<(https?://[^>]+)>'`) rencontrée dans le texte — couleur du thème courant (`theme.get("link", "#0066cc")`).
- Les 2 premiers paragraphes sont mis en gras (`bold = "font-weight:bold;" if p_idx <= 1 else ""`) — convention pour mettre en avant l'introduction du texte de licence.

`_on_link_clicked` ouvre les liens externes via `QDesktopServices.openUrl` (le navigateur système, pas une visionneuse interne) — **`setOpenLinks(False)`** sur le `QTextBrowser` pour empêcher Qt de tenter une navigation interne d'abord.

Le bouton "Voir la licence complète" (`_open_full_license`) appelle toujours `show_full_license_window_qt` — **uniquement la GPL**, jamais les 4 autres licences tierces (voir section suivante pour où celles-ci sont accessibles).

### `_FullLicenseDialog` — licence complète en lecture seule

Classe générique réutilisée pour les **5** licences complètes, chacune via sa propre fonction publique :

| Fonction | Fichier lu | Titre de fenêtre |
|---|---|---|
| `show_full_license_window_qt` | `LICENSE` (racine, via `resource_path`) | "GNU General Public License v3.0" |
| `show_full_unrar_license_window_qt` | `unrar/license.txt` | "UnRAR License" |
| `show_full_7zip_license_window_qt` | `7zip/license.txt` | "7-Zip License" |
| `show_full_piqad_license_window_qt` | `fonts/pIqaD-qolqoS-LICENSE.txt` | "pIqaD qolqoS — SIL Open Font License 1.1" |
| `show_full_tengwar_license_window_qt` | `fonts/AlcarinTengwar-LICENSE.txt` | "Alcarin Tengwar — SIL Open Font License 1.1" |

Lecture du fichier en `utf-8`, `QTextBrowser.setPlainText` (pas de HTML ici, texte brut tel quel). Gestion d'absence de fichier (`messages.errors.license_file_not_found.message`) et d'erreur de lecture (`messages.errors.license_file_read_error.message`) — les deux affichées **dans le corps du texte du dialogue lui-même**, pas via `ErrorDialog` séparée. Les 4 titres de fenêtre autres que GPL sont **des chaînes anglaises en dur, non traduites** (`"UnRAR License"`, `"7-Zip License"`, etc.) — cohérent avec le fait que ce sont des noms propres de licences tierces, pas du texte d'interface.

## Les 3 points d'entrée du copyright cliquable

Tous appellent `mw._show_license_dialog()` (`MosaicView.py:617-619`) → `show_license_dialog_qt(self._active_panel)` :

1. **Footer de la colonne d'icônes** (`icon_toolbar_qt.py:1585-1592`) — `_FooterLabel` avec `text=_("labels.copyright")`, `callback=self._callbacks.get("show_license_dialog")`. `_FooterLabel` (classe dédiée, `icon_toolbar_qt.py:352`) est un `QLabel` cliquable avec focus clavier (`Entrée`/`Espace` déclenchent le callback, `↑`/`↓` naviguent vers les autres éléments du footer via `_navigate_footer`) et un style de focus visible (bordure). `update_text()` retraduit le texte et réapplique la police (voir skill `fonts`) à chaque changement de langue.
2. **Menu contextuel** (`context_menus_qt.py:698`) — libellé en dur `"© Bruno Aublet 2025-2026"` (pas de clé de traduction, un copyright ne se traduit pas).
3. **Barre de menu** (`menubar_qt.py:541`, dans `_populate_about_menu`, voir skill `menu-bar`) — même libellé en dur, callback identique.

`mw._show_license_dialog()` est également exposée sur `panel_widget.py:905-906` (`PanelWidget._show_license_dialog` délègue à `self._main_window._show_license_dialog()`) — permet d'appeler la méthode depuis le contexte d'un panneau sans connaître `MainWindow` directement.

## Le sous-menu "Licences" — accès direct aux 5 licences complètes

Distinct du bouton "Voir la licence complète" du résumé : dans le menu À propos (`_populate_about_menu`, `menubar_qt.py:543-550`, voir skill `menu-bar`), un sous-menu **"Licences"** liste directement les 5 fonctions `show_full_*_license_window_qt`, sans jamais passer par `_LicenseDialog`. Callbacks câblés dans `menubar_callbacks_qt.py:167-171`. C'est le **seul** chemin d'accès aux 4 licences tierces (UnRAR/7-Zip/pIqaD/Tengwar) — le résumé cliqué depuis le copyright ne mène jamais qu'à la GPL.

## Comment modifier

- **Changer le texte du résumé de licence** : clé de traduction `labels.license_text` (voir skill `add-translation`) — respecter la convention de mise en forme (lignes tout en majuscules pour un titre de section, URL entre chevrons `<https://...>` pour un lien cliquable) puisque `_populate()` la parse par heuristique, pas par balisage explicite.
- **Ajouter une nouvelle licence tierce complète** (ex. nouvelle dépendance embarquée) : ajouter une fonction `show_full_xxx_license_window_qt(parent)` suivant le pattern des 5 existantes, l'ajouter au sous-menu Licences (`menubar_qt.py::_populate_about_menu`) et au dict de callbacks (`menubar_callbacks_qt.py`) — voir aussi skill `check-embedded-versions` pour où les versions de ces dépendances sont par ailleurs suivies.
- **Changer le fichier source d'une licence complète** : le chemin est en dur dans chaque fonction (`resource_path(os.path.join(...))`) — `resource_path` gère la résolution PyInstaller vs exécution non compilée (voir skill `fonts` pour un autre usage de cette même fonction).

## Pièges connus

- **Le bouton "Voir la licence complète" du résumé ne mène qu'à la GPL** — un utilisateur cherchant la licence UnRAR/7-Zip/pIqaD/Tengwar depuis le copyright cliquable ne la trouvera jamais par ce chemin ; elle n'est accessible que via le sous-menu "Licences" de À propos, une UI complètement séparée.
- **Les 4 titres de fenêtre non-GPL sont non traduits** (chaînes anglaises en dur) — ne pas chercher de clé de traduction pour `"UnRAR License"` etc., il n'y en a pas, contrairement à la règle générale CLAUDE.md "aucun texte codé en dur" qui s'applique au reste de l'UI (exception délibérée pour des noms propres de licences).
- **Erreurs de lecture de fichier affichées dans le corps du dialogue, pas via `ErrorDialog`** — `_FullLicenseDialog` remplace simplement le contenu du `QTextBrowser` par un message d'erreur au lieu d'ouvrir une fenêtre d'erreur séparée ; à garder en tête si une évolution future veut uniformiser la gestion d'erreur avec le reste du projet.
- **`is_heading()` est une heuristique fragile** — toute ligne entièrement en majuscules de plus de 2 caractères et ne commençant pas par `http` est traitée comme un titre de section ; un texte de licence traduit dans une langue où la casse fonctionne différemment (ou contenant un acronyme en majuscules au milieu d'une phrase normale) pourrait produire un découpage visuel incorrect — à vérifier si un jour `labels.license_text` est traduit dans une langue non latine.

## Références croisées

- `add-translation` — clé `labels.license_text`, seule source du contenu du résumé de licence.
- `fonts` — `resource_path()`, réutilisée ici pour résoudre les chemins des fichiers de licence embarqués.
- `check-embedded-versions` — suivi des versions des logiciels tiers (7-Zip, UnRAR) dont les licences sont affichées ici.
- `menu-bar` — sous-menu "Licences" dans À propos, seul point d'accès direct aux 4 licences tierces.
- `icon-toolbar` — `_FooterLabel`/`_FooterBtn`, widgets du footer de la colonne d'icônes (copyright + boutons taille/config), navigation clavier `_navigate_footer`.
- `qt-tooltips` / `qt-context-menus` — `setup_text_browser_context_menu`, menu contextuel appliqué aux deux `QTextBrowser` de ce fichier.
