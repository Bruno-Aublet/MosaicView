---
name: file-close
description: Localiser ou modifier la fermeture de fichier/application MosaicView (croix de fermeture, dialogues de confirmation de perte de modifications). Utiliser dès qu'une tâche touche à file_close_qt.py, close_file/force_close_file, ou CloseWarningDialog.
---

# Fermeture de fichier / application — MosaicView

Toute la logique de la croix de fermeture (et du menu "Quitter", qui déclenche le même `closeEvent`) : décider si on ferme juste le comic ouvert ou toute l'application, et quel dialogue de confirmation afficher selon l'état de modification. Un seul fichier : `modules/qt/file_close_qt.py`.

## Les 3 fonctions en cascade

```
on_window_close()   ← appelée depuis MosaicView.py::closeEvent (et par mw.close() via le menu Quitter)
  └─ close_file()    ← décide quel dialogue afficher selon l'état
       └─ force_close_file()   ← fermeture effective (libération mémoire, reset du canvas)
```

### `force_close_file()` — fermeture effective, sans confirmation

Fonction "bas niveau" : ne pose aucune question, exécute directement. Libère la mémoire de **toutes** les entrées (`images_data` + `all_entries` sans doublon, dédupliquées par `id()`) : ferme les objets PIL (`img`, `large_thumb_pil`) puis met `None` sur `bytes`/`img_id`/`qt_pixmap_large`/`qt_qimage_large`. Réinitialise l'état (`current_file`, `images_data`, `all_entries`, `selected_indices`, `comic_metadata`, `_page_attrs_by_entry_id`, `modified`, `needs_renumbering`, `merge_counter`, `zip_compression_state`, `current_directory`) et appelle `reset_history(state)` (voir skill `undo-redo` — l'historique undo/redo ne doit jamais survivre à la fermeture d'un comic).

Deux `gc.collect()` consécutifs (le second pour les cycles de références croisées Python/Qt qu'un seul passage ne détecte pas toujours) puis `shutdown_pdf_process()` (voir skill `pdf-loading`) — la fermeture d'un comic tue systématiquement les process PyMuPDF préchauffés, qu'un PDF ait été impliqué ou non.

### `close_file()` — décision + dialogue de confirmation

Retourne un booléen synchrone : `True` si la fermeture a eu lieu immédiatement (pas de confirmation nécessaire), `False` si un dialogue **non-modal** a été ouvert et que la suite se joue en asynchrone via callback. L'appelant ne doit jamais supposer que `False` signifie "annulé" — ça signifie juste "en attente d'une réponse utilisateur".

Quatre cas, dans cet ordre :

1. **Pas d'archive (`current_file` vide) mais images présentes** :
   - Non modifié → ferme directement (`_force()`), retourne `True`.
   - Modifié → `CloseWithoutSaveDialog` (Oui/Non/Annuler) : Oui = `create_cbz_cb()` puis ferme **seulement si** `state.current_file` a été renseigné (l'utilisateur peut annuler la création du CBZ dans la sous-boîte de dialogue "Enregistrer sous", auquel cas on ne ferme pas) ; Non = ferme sans sauvegarder ; Annuler = ne fait rien. Retourne `False`.
2. **Pas d'archive, pas d'images** — canvas déjà vide : ferme silencieusement (appelle `on_closed()` si fourni), retourne `True`. Pas de `_force()` ici car il n'y a rien à libérer.
3. **Archive présente, non modifiée** : ferme directement, retourne `True`.
4. **Archive présente et modifiée** : `CloseWarningDialog` (3 boutons empilés verticalement — voir modèle CLAUDE.md "Dialogs de confirmation de fermeture/sauvegarde") : "Fermer sans sauvegarder" (rouge) = `_force` direct ; "Sauvegarder le CBZ"/"Créer un CBZ" (vert, libellé selon que l'archive est déjà `.cbz` ou non — `self._is_cbz`) = `apply_new_names_cb(on_complete=...)` (voir skill `save-export`) puis `_force()` **différé d'un tick** (`QTimer.singleShot(0, _force)`) seulement si `success is not False` ; "Annuler" (gris) = ne fait rien. Retourne `False`.

Le paramètre `on_closed` (callable optionnel) est appelé une fois la fermeture **effective** — immédiatement en cas de retour synchrone `True`, ou après résolution du dialogue si asynchrone. **Jamais appelé si l'utilisateur annule** — un appelant qui fournit `on_closed` ne doit rien enchaîner après l'appel à `close_file()` lui-même, toute la suite doit vivre dans `on_closed`.

### `on_window_close()` — point d'entrée depuis `closeEvent`

Appelée depuis `MosaicView.py` (dans le `closeEvent` de la fenêtre principale) et indirectement par le menu contextuel/barre de menu "Quitter" (`context_menu.canvas.quit` → callback `on_window_close` = `mw.close` dans `menubar_callbacks_qt.py:68`, qui déclenche le `closeEvent` Qt standard plutôt que d'appeler cette fonction directement).

Logique :
- **Canvas non vide ou modifié** (`state.images_data or state.modified`) → délègue à `close_file()`. Si `close_file()` retourne `False` (dialogue ouvert), retourne `False` immédiatement (l'appli ne se ferme pas, `closeEvent` doit ignorer l'événement). Si `True` et qu'il y **avait** une archive ouverte (`had_archive`), nettoie les fichiers temporaires (`cleanup_temp_cb`) mais retourne quand même `False` — **la fenêtre elle-même ne se ferme pas**, seul le comic actuel se ferme (l'appli reste ouverte, canvas vide). Si `True` et qu'il n'y avait **pas** d'archive, sauvegarde la session (`save_session_cb`, voir skill `session-restore`) et retourne `True` — cette fois l'appli se ferme vraiment.
- **Canvas vide** (rien à fermer) → sauvegarde la session, nettoie les temporaires, retourne `True` directement — c'est le cas "clic sur la croix avec un canvas déjà vide" décrit dans le commentaire de tête de fichier ("Canvas vide → ferme l'application").

Autrement dit, **la valeur de retour `True`/`False` de `on_window_close` a deux significations différentes selon le chemin emprunté** : soit "l'appli peut se fermer", soit "un dialogue est en attente" — dans les deux cas `False`, mais pas pour la même raison structurelle (dialogue asynchrone vs "on vient de fermer le comic, pas l'appli"). Voir "Pièges" pour le risque associé.

## Les dialogues (tous non-modaux, tous dans ce fichier)

- **`CloseWarningDialog`** — 3 boutons empilés (rouge/vert/gris), suit très exactement le modèle documenté dans CLAUDE.md ("Dialogs de confirmation de fermeture/sauvegarde — modèle de boutons") : `_BTN_STYLE` identique, `setFixedHeight(80)`, couleurs `#ff9999`/`#99ff99`/`#cccccc`. Le libellé du bouton vert dépend de l'extension du fichier actuel (`_is_cbz`).
- **`CloseWithoutSaveDialog`** — même modèle de 3 boutons, pour le cas "pas d'archive, juste des images en vrac modifiées". `keyPressEvent` gère `Escape` comme "Annuler" explicitement (pas par défaut Qt).
- **`DeleteConfirmDialog`** — dialogue de confirmation de suppression générique (Oui/Non), **distinct** des deux ci-dessus par son usage (suppression de fichiers, pas fermeture) mais réutilisé depuis ce module ; ne suit **pas** le modèle 3-boutons-empilés de CLAUDE.md (2 boutons côte à côte) — à ne pas confondre si on modifie l'un en pensant à l'autre.

## Comment modifier

- **Ajouter une nouvelle condition de fermeture** (ex. un 5e cas dans `close_file`) : respecter l'ordre des `if` existants — les 4 cas sont mutuellement exclusifs et couvrent déjà tout l'espace `(current_file vide/présent) × (images_data vide/présent) × (modified oui/non)`, donc un nouveau cas doit se glisser dans cette grille plutôt que la contourner.
- **Changer ce qui est libéré à la fermeture** : `force_close_file()`, la liste de clés dans les deux boucles `for key in (...)`. Ne pas oublier que **tout ajout d'une nouvelle clé d'entrée porteuse de mémoire lourde** (nouvel objet PIL, nouveau cache Qt) devrait être ajouté ici pour éviter une fuite mémoire à la fermeture.
- **Changer le comportement du bouton "Sauvegarder" dans `CloseWarningDialog`** : ne touche pas ce fichier — voir skill `save-export`, `apply_new_names_cb` est injecté depuis l'extérieur (`panel_widget.py`).
- **Ajouter un nettoyage supplémentaire à la fermeture d'un comic** (pas de toute l'appli) : `force_close_file()`, après le `reset_history` — c'est le seul endroit garanti d'être appelé dans les 4 cas de `close_file()`.

## Pièges connus

- **Le retour `False` de `on_window_close` a deux significations différentes** (dialogue en attente vs "comic fermé mais appli restée ouverte") — un appelant qui interprète `False` comme "annulé, ne fais rien d'autre" se tromperait dans le second cas : le nettoyage temp a déjà eu lieu, seule la fenêtre ne s'est pas fermée. Si une évolution future doit distinguer ces deux cas, il faudrait changer la signature (actuellement un simple booléen ne suffit pas à les différencier depuis l'extérieur).
- **`on_closed` n'est jamais appelé en cas d'annulation** — ne pas enchaîner du code après `close_file(..., on_closed=...)` en supposant qu'il s'exécutera toujours ; toute la suite doit être dans `on_closed` lui-même.
- **Le bouton "Créer un CBZ" peut échouer silencieusement sans fermer** — dans `close_file`, cas 1 (`_yes`), si `create_cbz_cb()` ouvre son propre sous-dialogue "Enregistrer sous" et que l'utilisateur l'annule, `state.current_file` reste vide et `_force()` n'est jamais appelé — le comic reste donc ouvert malgré le clic sur "Oui, créer un CBZ", sans message d'erreur explicite à ce niveau (le sous-flux de `save-export` gère ses propres messages).
- **`QTimer.singleShot(0, _force)` dans `_apply_and_close`** — le délai d'un tick n'est pas cosmétique : appeler `_force()` de façon synchrone dans la continuation d'`apply_new_names_cb` risquerait de détruire des objets Qt encore référencés par la pile d'appels en cours (le dialogue de succès de `save-export` qui vient de se fermer, par exemple) — garder ce différé si on retouche ce chemin.
- **`DeleteConfirmDialog` n'est pas un dialogue de fermeture** malgré sa présence dans ce fichier — ne pas le modifier en croyant affecter le comportement de la croix de fermeture.

## Références croisées

- `undo-redo` — `reset_history(state)`, appelé systématiquement dans `force_close_file`.
- `pdf-loading` — `shutdown_pdf_process()`, appelé systématiquement dans `force_close_file` (que le comic fermé ait été un PDF ou non).
- `save-export` — `create_cbz_cb`/`apply_new_names_cb`, injectés depuis l'extérieur et appelés depuis les callbacks des dialogues de ce fichier.
- `session-restore` — `save_session_cb`, appelé par `on_window_close` uniquement quand l'application se ferme réellement (pas quand seul un comic se ferme).
- `temp-files` — `cleanup_temp_cb`, appelé par `on_window_close` dans les deux branches où une fermeture a eu lieu (comic ou appli).
- `qt-context-menus` — entrée "Quitter" du menu contextuel canvas, qui route vers `mw.close()` plutôt que d'appeler `on_window_close` directement.
