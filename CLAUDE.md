# Règles de comportement — MosaicView (priorité absolue)

Ces règles s'appliquent en permanence, sur toute tâche, sans qu'il soit nécessaire de les invoquer. Elles priment sur toute autre considération de style ou de commodité.

## Skills disponibles — à invoquer systématiquement quand le sujet correspond

Avant d'improviser une procédure ou de la reconstruire depuis zéro, vérifier si un skill existant couvre déjà le sujet et l'invoquer :

- **`panels`** — panneaux panel1/panel2 en split-view, panneau virtuel de la bibliothèque
- **`bump-version`** — bump de version (4 fichiers), sur demande explicite uniquement
- **`add-translation`** — ajout/modification de clés de traduction dans `locales/*.json`
- **`verify-translation`** — audit qualité d'une traduction existante (par rotation)
- **`fonts`** — gestion des polices (`get_current_font()`, pIqaD/Tengwar, offset global)
- **`qt-context-menus`** — menus contextuels Qt (clic droit)
- **`qt-tooltips`** — tooltips (`OverlayTooltip` obligatoire, jamais `setToolTip()`)
- **`undo-redo`** — le système Annuler/Refaire
- **`apply-image-operation`** — pattern obligatoire pour tout changement de `entry['bytes']`
- **`rotate-flip`** — rotation 90° et miroir de la mosaïque
- **`page-straighten`** — redressement d'image, manuel (rotation libre) ou automatique (deskew, détection d'inclinaison par transformée de Hough)
- **`add-text-to-image`** — ajout de texte riche sur une image
- **`draw-shapes`** — dessin de formes géométriques (ellipse, rectangle, rectangle à coins arrondis, ligne, flèche) sur une image, dans la visionneuse principale
- **`clone-zone`** — tampon de clonage dans la visionneuse principale
- **`page-resize`** — redimensionnement des pages, détection des pages multiples
- **`page-crop`** — recadrage dans la visionneuse principale
- **`create-ico`** — création de fichiers `.ico` multi-résolution
- **`animated-gif`** — création/édition de GIF animé
- **`nfo-editor`** — création/édition de fichiers `.nfo`
- **`corrupted-images`** — détection/remplacement des images corrompues
- **`canvas-overlay-progress`** — **mécanisme central obligatoire** des labels rouges de progression en surimpression, ne jamais recréer à la main
- **`explorer-select`** — ouvrir l'Explorateur avec focus sur un fichier (`_explorer_select`)
- **`check-embedded-versions`** — versions des logiciels embarqués (7-Zip, UnRAR, PyMuPDF, Pillow)
- **`user-guide`** — le mode d'emploi (fenêtre d'aide "?")
- **`wilhelm-scream-easter-egg`** — l'easter egg sonore du cri de Wilhelm
- **`viewers`** — la visionneuse plein-écran de lecture, seule restante, et sa barre d'outils flottante (13 outils migrés, plus aucune fenêtre plein écran annexe — MAIS chantier de fusion PAS terminé : 3 fonctions du panneau Ajustements classique doivent encore y migrer, voir `adjustments-panel`)
- **`adjustments-panel`** — la fenêtre "Ajustements d'image" elle-même (orchestration), réduite à 3 sections (profondeur de couleur, effets, mode d'image)
- **`adjust-color-depth`** / **`adjust-compression`** / **`adjust-sharpness`** / **`adjust-brightness-contrast`** / **`adjust-levels`** / **`adjust-transparency`** / **`adjust-image-mode`** / **`adjust-remove-colors`** / **`adjust-saturation`** / **`adjust-effects`** — logique PIL de chaque fonction d'ajustement d'image (`adjust-color-depth`/`adjust-image-mode`/`adjust-effects` vivent dans le panneau Ajustements classique ; `adjust-sharpness`/`adjust-brightness-contrast`/`adjust-saturation`/`adjust-remove-colors`/`adjust-compression`/`adjust-levels`/`adjust-transparency` ont toutes migré dans la barre d'outils de la visionneuse principale, voir skill `viewers`)
- **`bookmarks`** — marque-pages (stockage global partagé entre panneaux)
- **`zip-compression`** — mode/niveau de compression ZIP des CBZ
- **`duplicate-detection`** — détection des pages en double (MD5)
- **`mosaic-thumbnails`** — la grille de vignettes (rendu, tailles, caches)
- **`archive-image-loading`** — chargement CBZ/CBR/CB7/CBT/EPUB et images isolées
- **`pdf-loading`** — chargement PDF (multiprocess dédié)
- **`printing`** — impression Windows
- **`save-export`** — les 6 méthodes de sauvegarde/export CBZ
- **`image-format-conversion`** — conversion de format des images sélectionnées
- **`file-close`** — fermeture fichier/application et confirmations
- **`open-with-default-app`** — ouverture d'un non-image avec l'appli par défaut
- **`menu-bar`** — la barre de menus horizontale
- **`update-checker`** — mise à jour de MosaicView depuis GitHub Releases
- **`flatten-directories`** — aplatissement de l'arborescence des répertoires
- **`keyboard-navigation`** — navigation clavier TAB entre zones
- **`recent-items`** — fichiers récents et bases récentes
- **`license-window`** — la fenêtre de licence
- **`window-title`** — le titre de la fenêtre principale
- **`web-import`** — import d'images depuis le web
- **`scan`** — numérisation d'images depuis un scanner physique (WIA)
- **`drag-and-drop`** — le drag & drop de la mosaïque
- **`clipboard`** — copier/couper/coller système (Ctrl+C/X/V)
- **`minimap`** — la minimap latérale
- **`icon-toolbar`** — la colonne d'icônes verticale
- **`library`** — la Bibliothèque (base `.mvdb`)
- **`comicvine-metadata-fetch`** — métadonnées en ligne depuis l'API ComicVine
- **`comicinfo-metadata-editor`** — édition locale du `ComicInfo.xml`
- **`tabs`** — les onglets mosaïque/métadonnées
- **`page-merge`** — fusion/jointure de pages
- **`page-split`** — scission d'une page en N parties
- **`renumbering`** — renumérotation des noms de fichiers (pages)
- **`sort-images`** — tri des images de la mosaïque
- **`status-bar`** — la barre de statut de chaque panneau
- **`dark-mode`** — mode sombre/clair et pattern `_apply_theme()`
- **`session-restore`** — restauration de session au démarrage
- **`single-instance`** — instance unique (named pipe)
- **`app-registration`** — déclaration de l'appli dans le registre Windows
- **`batch-processing`** — architecture commune des traitements par lot
- **`batch-cbr-convert`** / **`batch-cb7-convert`** / **`batch-cbt-convert`** / **`batch-pdf-convert`** / **`batch-img-convert`** / **`batch-recompress`** / **`batch-metadata-import`** / **`batch-library-create`** — le détail de chacun des 8 flux batch
- **`config-storage`** — configuration persistante (`%APPDATA%`)
- **`temp-files`** — fichiers jetables (`%TEMP%\MosaicViewTemp`)

Ne pas se fier uniquement à la mémoire ou à ce fichier pour ces sujets : les skills contiennent le détail opérationnel à jour et sont la source de vérité pour leur procédure respective.

## Architecture

- Point d'entrée Qt actif : `MosaicView.py` (racine). Modules dans `modules/qt/`. tkinter (`MosaicView_OLD.py`) supprimé.
- Visionneuse/éditeur de mosaïques d'images (CBZ, CBR, CB7, PDF), PIL/Pillow. Dossiers : `icons/`, `fonts/`, `locales/`, `modules/qt/`, `paypal/`, `unrar/`, `7zip/`.
- `modules/qt/utils.py::format_file_size(size_bytes)` (o/Ko/Mo/Go/To)

## Règles UI Qt obligatoires (toute fenêtre/widget, sans exception)

**S'appliquent à TOUTES les fenêtres Qt (QDialog, QWidget, modale ou non, peu importe son rôle), sans aucune exception.**

1. **Mode sombre / thème** : tous les widgets doivent respecter le thème courant (couleurs fond/texte/bordures via stylesheet ou palette Qt). Mise à jour dynamique quand le thème change.
2. **Changement de langue à la volée** : tous les textes visibles (labels, boutons, titres, tooltips) doivent se mettre à jour immédiatement quand la langue change, via `language_signal.changed` (connecté dans `__init__`, déconnecté dans `finished`/`_on_close`). Ne jamais supprimer cette connexion sous prétexte qu'une fenêtre est modale — une modale peut rester ouverte pendant un changement de langue.
   - **Règle absolue, sans dérogation sauf demande expresse de l'utilisateur** : aucun texte codé en dur qui ne se retraduit pas dynamiquement. Ça inclut les textes de statut écrits hors de `_retranslate()` (`.setText(...)` appelé depuis un callback, un worker, un handler d'erreur, etc.) — s'ils ne sont pas rejoués dans `_retranslate()`, ils restent figés dans l'ancienne langue si l'utilisateur change de langue pendant qu'ils sont affichés.
   - **Fix systématique** : ne jamais stocker/passer un texte déjà résolu (`str`) pour un statut qui peut rester affiché ; toujours stocker un **callable `() -> str`** (ex. `self._status_text_fn = lambda: _("clé")`) via une méthode `_set_status(status_fn)`, et rappeler `self._status_text_fn()` dans `_retranslate()` pour rafraîchir le label. S'applique à tout texte qui transite par un signal Qt, un thread worker, ou une fonction intermédiaire avant affichage.
   - **Piège deleteLater() sans `finished`** : si un dialogue non-modal se ferme via `hide()` + `deleteLater()` au lieu de `accept()`/`reject()`/`close()`, le signal `finished` **n'est PAS émis**. Si la déconnexion de `language_signal.changed` est câblée sur `self.finished.connect(...)`, elle ne se déclenche jamais → `RuntimeError: Internal C++ object already deleted` à chaque changement de langue. Dans `_finish` ET `closeEvent` d'un tel dialogue, déconnecter **explicitement** le signal de langue AVANT `deleteLater()` — ne jamais compter sur `finished` seul.
   - **Piège retraduction dynamique** : retraduire un widget construit dynamiquement en appelant juste `widget.setText(_('clé'))` change le texte mais garde l'ancienne police (latine) pour les langues tengwar/piqad → glyphes illisibles. Dans toute fonction de retraduction, réappliquer SYSTÉMATIQUEMENT `widget.setFont(get_current_font(taille))` en plus de `setText`. Ne jamais faire `setFont` une seule fois à la création pour un widget dont le texte change de langue (voir aussi section QMenu/QMenuBar du skill `qt-context-menus` pour le cas où un stylesheet écrase `setFont`).
3. **`get_current_font()`** : tous les textes doivent utiliser `get_current_font()` (ou équivalent Qt) pour la police courante. Mise à jour dynamique quand la police change.
4. **Non-modale par défaut, JAMAIS, NULLE PART, SANS LA MOINDRE EXCEPTION** : ça inclut les dialogues question-réponse (Oui/Non/Annuler), Error, Info, confirmation, saisie — tout.
   - **Why (raison architecturale, pas cosmétique)** : l'application a une fonction de double interface — deux panneaux séparés. Une fenêtre modale gèle toute l'application, donc bloque aussi l'autre panneau. Une modale = architecture cassée. Non négociable.
   - **Interdits absolus** : `setModal(True)`, `.exec()`, `.ask()` basé sur `.exec()`, `setWindowModality(Qt.ApplicationModal)` ou `Qt.WindowModal`. Un `QEventLoop` local qui bloque le flux compte aussi comme modal.
   - **Comment faire** : `setModal(False)` + `setWindowModality(Qt.NonModal)` + `show()`/`raise_()`/`activateWindow()`. Les dialogues question-réponse retournent leur résultat via **callback/signal**, jamais via une valeur de retour synchrone obtenue par `.exec()`.
   - **Textes dans ErrorDialog/InfoDialog** : toujours passer des lambdas, jamais des strings figées, même pour les messages avec paramètres dynamiques : `InfoDialog(self, lambda: _wt('clé.titre'), lambda c=count, p=path: _('clé.message', count=c, path=p))`.
   - Seule preuve valable que la règle tient : `tests/test_check_no_modal.py` (via `python -m pytest tests/test_check_no_modal.py`) passe sans erreur sur `modules/qt/`.
5. **Centrée sur le panneau source, sans flash** : toute nouvelle fenêtre doit être centrée sur le panneau Qt qui l'a déclenchée (via `_center_on_widget(dialog, parent_panel)` dans `modules/qt/dialogs_qt.py`), pas sur la fenêtre principale.
   - **Piège** : centrer uniquement via `showEvent` + `QTimer.singleShot(0, ...)` affiche d'abord la fenêtre à sa position par défaut puis la déplace au tick suivant → flash visible au mauvais endroit.
   - **Fix correct** : `position_dialog_on_parent(dialog, parent)` (dans `dialogs_qt.py`) **avant** `show()`, pas dans `showEvent`. Pattern : une méthode `show_nonmodal()` qui fait `position_dialog_on_parent(self, self._center_parent); self.show(); self.raise_(); self.activateWindow()`. Ne JAMAIS utiliser `adjustSize()` dans ce helper — ça redimensionne à la taille minimale du contenu et écrase une taille explicite (`resize(...)`/`setFixedSize`) ; utiliser `ensurePolished()` qui finalise le layout sans changer la taille demandée.
   - `MsgDialog`, `InfoDialog`, `ErrorDialog` dans `dialogs_qt.py` ont déjà cette méthode `show_nonmodal()` — l'appeler au lieu de `.show()` nu.
   - **Piège second dialogue déclenché par un premier qui se ferme aussitôt** : quand un dialogue A affiche un dialogue B (non-modal) **puis se ferme immédiatement** (`self.accept()`/`self.close()` dans la foulée), ne pas centrer B sur A — le centrage différé tire après que A a disparu → géométrie nulle → B mal placé. Passer `self.parent()` (le panneau source, parent Qt de A) comme parent de B, pas `self`.
   - **Piège QLabel WordWrap dans un dialogue à largeur fixe** : un `QLabel` avec `setWordWrap(True)` multi-lignes a un `sizeHint` qui sous-estime sa hauteur (calcul comme sur une seule ligne large) → `adjustSize()` réserve trop peu de hauteur, dernières lignes coupées. Quand un dialogue a `setFixedWidth(W)` et un QLabel WordWrap multi-lignes, imposer `label.setMinimumHeight(label.heightForWidth(W - marges))` dans `_retranslate` (pour suivre langue/police) plutôt que de compter sur `adjustSize()`/`sizeHint` seuls.
6. **Menu contextuel Qt** : toujours remplacer le menu natif par un menu traduit/thémé (voir skill qt-context-menus).
7. **Titre de fenêtre = `_wt()`, JAMAIS `_()`** : tout texte qui finit dans `setWindowTitle()` doit être résolu via `_wt(key)` (repli latin pour les langues CSUR pIqaD/Tengwar, illisibles en barre de titre Windows). Ça inclut les **titres passés en callable à `InfoDialog`/`ErrorDialog`/`MsgDialog`/confirm** — le callable doit être `lambda: _wt("...")`, pas `lambda: _("...")`.
   - **Piège** : ne jamais copier un call-site existant sans vérifier qu'il utilise `_wt()` pour le titre — plusieurs anciens appels utilisent encore `_()` par erreur.
8. **Tooltips** : TOUJOURS et OBLIGATOIREMENT réutiliser le mécanisme unique déjà en place dans l'appli (`OverlayTooltip`), JAMAIS en recréer un nouveau, JAMAIS `QToolTip`/`setToolTip()` natif Qt — voir skill `qt-tooltips` pour le détail opérationnel (canvas vs widget de QDialog vs cellule de tableau, format HTML, instanciation).

**Avant de considérer une fenêtre comme terminée, vérifier explicitement ces 8 points. Ne jamais affirmer qu'une fenêtre est corrigée sans avoir relu le code complet de cette fenêtre.**

### Détails de style annexes
- Éviter les couleurs vives (orange/rouge) pour un avertissement non bloquant — préférer la couleur de texte du thème en gras+italique ; réserver le rouge aux vrais messages d'erreur/statut.
- Centrer systématiquement les textes/labels d'une fenêtre de dialogue simple type formulaire (`Qt.AlignCenter`), sauf besoin explicite d'alignement à gauche.

## Sécurité — URLs et secrets externes

Pour toute donnée provenant d'un fichier externe potentiellement non fiable (ComicInfo.xml d'un CBZ téléchargé, ou toute donnée d'origine externe affichée/ouverte) :

1. **Jamais `setOpenExternalLinks(True)` sur un QLabel affichant une URL issue de métadonnées externes.** Un CBZ malveillant peut mettre dans le champ `Web` un chemin UNC (vecteur de vol de hash NTLM), un `file://`, ou un protocole custom. Toujours `setOpenExternalLinks(False)` + `linkActivated.connect(modules.qt.utils.open_url)` (filtre le schéma, n'autorise que `http`/`https`). Ne s'applique pas aux URLs fixes en dur dans le code (liens de crédits), seulement aux données lues depuis un fichier externe.
2. **Filtrer les secrets (clé API, tokens) des messages d'erreur réseau avant affichage utilisateur.** Les exceptions réseau incluent souvent l'URL complète avec paramètres sensibles en clair — voir `comicvine_scraper.py::_redact_api_key()` comme pattern de référence.

## Dialogs de confirmation de fermeture/sauvegarde — modèle de boutons

Pour tout dialog de type "fermer sans sauvegarder" (ex. `CloseWithoutSaveDialog`), suivre le modèle de `CloseWarningDialog` :
- Disposition : colonne verticale (pas ligne horizontale).
- Hauteur des boutons : `setFixedHeight(80)`.
- Style : `_BTN_STYLE` avec `background-color`, `color: #000000`, `font-size: 13pt`, `border: 2px groove #888888`.
- Couleurs : Oui/Créer = vert `#99ff99` / `#77ff77`, Non/Fermer sans sauver = rouge `#ff9999` / `#ff7777`, Annuler = gris `#cccccc` / `#aaaaaa`.
- Titre : centré, `font-size: 20px; font-weight: bold;`, couleur `#d9534f`, `_get_current_font(13, bold=True)`.
- Message : centré, `_get_current_font(13)`.
- Taille fenêtre : assez grande pour les 3 boutons (ex. 500×400).

**Why** : cohérence visuelle forte attendue entre toutes les fenêtres de confirmation de fermeture/perte de données.

## Règles générales de collaboration

- **Fusion des visionneuses (idees.txt #3, chantier PAS TERMINÉ malgré l'avancement au 2026-08-16) — NE JAMAIS migrer le code d'un outil dans `image_viewer_qt.py`.** Les 13 outils déjà migrés (crop, redressage, clonage, texte, netteté/netteté adaptative, luminosité-contraste, saturation, suppression des couleurs, compression, niveaux, transparence) et le 14e outil neuf (formes) ont chacun leur logique dans leur propre module séparé (ex. `crop_tool_qt.py`, `straighten_tool_qt.py`, `clone_tool_qt.py`, `transparency_tool_qt.py`) — `image_viewer_qt.py` orchestre/appelle ces modules (état minimal de branchement, délégation), mais ne contient JAMAIS leur implémentation (calculs, gestion des événements souris propres à l'outil, rendu de l'overlay, panneaux flottants d'options). **Why** : consigne expresse de l'utilisateur donnée dès le début de ce chantier — sans cette séparation, `image_viewer_qt.py` grossit à chaque outil migré et devient ingérable une fois tous les outils fusionnés. **Règle absolue, sans exception, qui s'appliquera de la même façon aux 3 fonctions du panneau Ajustements classique (profondeur de couleur, effets, mode d'image) encore à migrer** : avant de toucher à un outil existant ou d'en ajouter un nouveau, vérifier explicitement que son code vit bien dans son propre module et pas dans `image_viewer_qt.py` — si ce n'est pas le cas (dette existante), le signaler et proposer une extraction avant de continuer, ne jamais l'aggraver en ajoutant davantage de code d'outil dans ce fichier. La visionneuse annexe d'ajustements plein écran (`AdjustmentViewerDialog`/`adjustments_viewers_qt.py`) a été intégralement supprimée (2026-08-16) — plus aucune fenêtre plein écran dédiée par outil — mais le panneau non-plein-écran `AdjustmentsDialog`/`adjustments_dialog_qt.py` existe toujours pour ces 3 fonctions restantes, voir skill `viewers` et `idees.txt` point 3.
- **Barre d'outils de la visionneuse principale — SÉLECTIONNER N'IMPORTE QUEL OUTIL FORCE TOUJOURS LE MODE SIMPLE PAGE, SANS AUCUNE EXCEPTION.** Implémenté en un seul point inconditionnel en tête de `_ViewerToolbar.set_active_tool` (`viewer_toolbar_qt.py`) : `if tool_id is not None and page_mode != "single": ...`. **Why** : le mode double/continu affiche un pixmap combiné de 2 pages ; un outil qui lit une position de clic ou une géométrie tracée peut la calculer par rapport à cette image combinée puis l'appliquer silencieusement à une seule page — bug réel vécu sur l'outil texte (blocs mal positionnés, en prod depuis v1.7.3, jamais repéré) et les pipettes de l'outil niveaux (couleur prélevée sur le mauvais pixel), découvert le 2026-08-15 lors de l'ajout de l'outil formes. **Règle absolue et définitive, décidée explicitement par l'utilisateur après plusieurs corrections faites au cas par cas jugées insuffisantes** : ne jamais réserver ce forçage à certains outils selon qu'ils semblent concernés ou non (y compris les outils sans aucun geste souris sur le canvas, et crop lui-même qui gérait déjà le cas autrement) — pour tout nouvel outil ajouté à cette barre, cette règle s'applique automatiquement sans qu'il soit besoin de l'implémenter à nouveau ni de la justifier.
- **Boutons "Valider" ET "Annuler" flottants de la visionneuse principale (crop/straighten/text/shapes/transparency) — UN SEUL MÉCANISME GÈRE LEUR RÉAPPARITION/DISPARITION, SANS EXCEPTION, POUR LES DEUX BOUTONS IDENTIQUEMENT.** Quatre fonctions aux responsabilités strictement séparées dans `image_viewer_qt.py` : `_update_validate_btn_state()`/`_update_cancel_btn_state()` recalculent texte/couleur/police/position mais n'appellent **jamais** `.show()`/`.hide()` (seules fonctions utilisables depuis n'importe quel point de mise à jour dispersé — tracé en cours, `display_image()`, validation, Échap... — toujours les deux ensemble, jamais l'une sans l'autre sous peine de désynchronisation visuelle) ; `_reveal_validate_btn()`/`_reveal_cancel_btn()`/`_conceal_validate_btn()`/`_conceal_cancel_btn()` sont les **seuls** points du code autorisés à afficher/masquer chaque bouton, appelés **uniquement** par `_ViewerToolbar` (`show_and_schedule_hide()`/`_on_hide_timeout()`/`set_active_tool()`). **Why** : une première version mélangeait mise à jour d'état et affichage dans une seule fonction, appelée depuis 15+ endroits — chacun devenait un point de réapparition/masquage indépendant du timer d'auto-masquage de la barre, ce qui produisait un bouton flottant seul (sans la barre au-dessus) ou un bouton refusant de disparaître ; **décision explicite et répétée de l'utilisateur** ("UN SEUL PUTAIN DE MECANISME UNIQUE pour gérer la réapparition de tout") après plusieurs correctifs partiels jugés insuffisants — le bouton "Annuler" (ajouté après coup, symétrique du bouton "Valider") a repris ce même mécanisme dès sa création plutôt que d'en réinventer un. **Règle absolue, sans exception** : ne jamais appeler `.show()`/`.hide()` sur l'un de ces deux boutons en dehors de ses `_reveal_*`/`_conceal_*` respectifs, et ne jamais ajouter un nouveau point d'appel à ces fonctions en dehors de `_ViewerToolbar`. Les deux boutons (`_ValidateButton`/`_CancelButton`) ont aussi leur propre `enterEvent`/`leaveEvent` (suspend le timer d'auto-masquage au survol, comme les panneaux d'options flottants, ET force le curseur normal — un widget `setEnabled(False)` ne reçoit plus ces événements, donc les deux restent **toujours** `setEnabled(True)`, seul le style change) — toute nouvelle zone flottante de cette barre doit suivre le même pattern.
- **Panneaux d'options flottants de la barre — BLINDAGE ANTI-FUITE DE CLIC OBLIGATOIRE (`mousePressEvent`/`mouseReleaseEvent` avec `event.accept()`), SANS EXCEPTION, POUR TOUT NOUVEAU PANNEAU.** Découvert le 2026-08-15/16 sur le panneau de l'outil transparence : un clic sur une zone vide d'un panneau flottant (marges non absorbées par un widget enfant) fuit vers `_ViewerCanvas` en dessous et peut déclencher un geste d'outil involontaire (ex. un clic pipette rendant un pixel transparent). **Why** : chaque panneau est un widget flottant enfant du canvas, pas un widget Qt indépendant — sans ce blindage, Qt route le clic non consommé au parent. Généralisé aux 12 panneaux existants après cette découverte. **Règle absolue** : tout nouveau panneau d'options de cette barre doit avoir ce blindage dès sa création, ne pas attendre qu'un bug le révèle.
- **NE JAMAIS changer le format de fichier** (JPG→PNG, etc.) sans permission explicite.
- **Ne jamais créer de nouveau fichier mémoire (auto memory) sans accord explicite de l'utilisateur.** Demander avant d'écrire quoi que ce soit dans le répertoire de mémoire persistante du projet (`.claude/projects/<slug-du-projet>/memory/` dans le profil utilisateur), y compris pour un bug/pattern qui semble évident à retenir.
- "En français pour l'instant" = clés de traduction en français, PAS du texte en dur.
- **Interdit absolu : texte codé en dur** — tout texte visible DOIT passer par `_("clé")`.
- **Ne jamais lancer l'application ni instancier Qt/QApplication soi-même**, même pour un test isolé ou en apparence anodin (ex. déclencher une `ErrorDialog` isolée pour tester un son). Toute commande qui importe `PySide6.QtWidgets`, instancie `QApplication`, ou construit/affiche un widget Qt doit être proposée à l'utilisateur pour qu'IL l'exécute lui-même. Si un test nécessite un chemin non atteignable dans l'appli normale, le dire honnêtement plutôt que d'improviser un lancement Qt de secours.
  - **Exception ponctuelle** : si l'utilisateur autorise explicitement un lancement Qt dans le fil de la conversation (ex. "je t'autorise à lancer Qt pour visualiser ce rendu"), c'est valable pour cette action précise, immédiatement. Ne pas redemander confirmation ni exiger une modification écrite de cette règle avant d'agir — l'accord donné dans la conversation suffit. Cette exception ne vaut que pour l'action explicitement autorisée, pas comme levée générale de la règle pour la suite de la session.
- **Suite de tests (`tests/`, pytest)** : c'est TOUJOURS l'utilisateur qui lance les tests, jamais Claude — ouvrir `run_tests.py` et cliquer sur ▶️ Run (ou `python -m pytest tests` en ligne de commande). Aucune installation ni instanciation Qt requise pour ces tests (modules purs + `tests/test_check_no_modal.py`, un scan AST statique). **Les 199 tests doivent être PASSED avant tout `git push`** — voir `RELEASE.txt` point 0bis. Si une modification de code touche un module couvert par `tests/`, rappeler à l'utilisateur de relancer la suite avant de pousser.
- **Après chaque step** : résumé + liste de tests, **ne pas enchaîner** sans accord express.
- **Reproduction de fichiers sources** : LIRE LE FICHIER ENTIER avant de reproduire un comportement — jamais de grep partiel.
- **Déboguer avec les données réelles** : lire les données exactes (JSON, valeurs) AVANT de coder, jamais après ; ne jamais supposer le contenu d'un texte/URL.
- **Ne jamais modifier du code hors du périmètre explicite de la demande.** Si la demande est "corriger la couleur des checkboxes", on touche uniquement aux checkboxes. Si on pense qu'autre chose devrait être changé, le signaler et attendre l'accord avant de toucher quoi que ce soit.
  - **Piège `_` écrasé** : ne jamais utiliser `_` comme variable/paramètre muet dans une fonction qui appelle aussi `_("clé")` — Python traite `_` comme variable locale dès qu'il voit une assignation, ce qui écrase la fonction de traduction importée au niveau module. Trois formes rencontrées : boucle (`for _ in range(n)`), déballage de tuple (`filepath, _ = QFileDialog.getSaveFileName(...)` → `UnboundLocalError`), paramètre par défaut (`def _on_op_changed(self, _=None)` → `TypeError: 'NoneType' object is not callable`). Utiliser `_i`, `_x`, `_unused`, `_filter`, `_idx`, `ignored`, etc. à la place.
- **Ne jamais modifier le code en réponse à une simple question.** Une question ("Tu as mis X ici ?") signale souvent que c'est voulu/attendu, pas une demande de correction. Répondre par du texte seulement ; n'écrire du code que sur instruction explicite de modifier quelque chose.
- **Toujours diagnostiquer avec des prints avant d'appliquer un fix**, même si la cause semble évidente. Dès qu'un bug implique un flux d'événements (drop, click, signal...), ajouter des prints dans tous les handlers candidats, attendre les logs de l'utilisateur, appliquer le fix seulement ensuite au bon endroit. Ne jamais retirer les prints avant confirmation explicite que le fix fonctionne.
- **`idees.txt`** : ne jamais marquer un point comme `[FAIT]`, ne jamais y renvoyer vers le CHANGELOG, ne jamais y laisser de trace qu'une idée a été implémentée. Quand une idée est implémentée : la retirer entièrement du fichier (supprimer, pas annoter). Le détail de ce qui a été fait va uniquement dans `CHANGELOG.md`. Si l'idée avait plusieurs sous-parties et qu'une seule est faite, ne garder que les parties non faites, reformulées proprement.
- **Nouvelle fenêtre d'erreur rare et grave = proposer le cri de Wilhelm.** Si la création ou la modification d'une fonction amène à créer/repérer une fenêtre `ErrorDialog` pour une erreur système rare et grave (échec d'écriture disque, erreur réseau imprévue, ressource interne manquante — voir skill `wilhelm-scream-easter-egg` pour le profil exact et les exemples existants), proposer à l'utilisateur d'y ajouter `play_sound=True`. Ne jamais l'ajouter automatiquement sans demander — c'est une proposition à faire en texte, pas une modification silencieuse. Ne pas proposer pour une simple validation utilisateur (champ vide, annulation, sélection invalide) : dans le doute, ne pas proposer.
- **Ne jamais lancer la procédure complète de bump de version** (skill `bump-version`) sur une simple demande de mise à jour du CHANGELOG — voir skill `.claude/skills/bump-version/SKILL.md`.
  - Ne même pas **proposer** le bump sur une demande de changelog seul — ça revient à réintroduire le versionnage par la porte de derrière. Ne pas non plus vérifier/grep les 3 autres fichiers de versionnage "au cas où".
  - Une entrée de changelog demandée sans instruction de version explicite = pas de numéro `[x.y.z]`, juste une entrée datée (ex. `## Website - YYYY-MM-DD - titre`).
- **Jamais de script Python/shell en Bash pour réécrire un fichier** — toujours l'outil Edit (contourne sinon la confirmation en mode manuel).
- **Ne jamais utiliser l'outil Agent** (subagent Explore, Plan, general-purpose, etc.). Sur la machine de l'utilisateur, l'invocation de l'outil Agent déclenche une ouverture du Windows Store, ce qui est extrêmement gênant. Toujours faire les recherches directement avec Glob, Grep, Read, Bash.
- **Skill obsolète ou erronée détectée en cours de route** : si une exploration de code ou une modification révèle qu'un skill existant n'est plus à jour et/ou contient des informations fausses (fichier renommé, fonction déplacée/supprimée, comportement décrit qui ne correspond plus au code), le signaler clairement et explicitement à l'utilisateur dans la réponse — ne pas se contenter de suivre le code réel en silence sans mentionner l'écart.
- **Modification touchant un sujet couvert par un skill** : après toute modification de code sur un élément couvert par un skill existant, demander à l'utilisateur s'il faut mettre à jour ce skill en conséquence. Ne jamais mettre à jour un skill de sa propre initiative sans poser la question.
- **Ajout ou suppression d'un skill** : à chaque ajout ou suppression d'un skill, (1) mettre à jour la section « Developer documentation (Claude Code skills) » du README pour qu'elle indique le bon nombre de skills, et (2) mentionner l'ajout ou la suppression dans le CHANGELOG.
- **Création d'un nouveau skill — contenu à couvrir** : expliquer où est la fonction, ce qu'elle fait, quand elle le fait, comment elle le fait, et comment la modifier. Faire des référencements croisés avec d'autres skills existants quand c'est pertinent (bidirectionnels si les deux sujets se recoupent réellement). Puis ajouter le skill au catalogue de ce fichier (une ligne courte : nom + accroche, voir le format des entrées existantes).
