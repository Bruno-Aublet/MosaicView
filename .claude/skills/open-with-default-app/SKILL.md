---
name: open-with-default-app
description: Localiser ou modifier l'ouverture d'un fichier non-image avec l'application Windows par défaut (double-clic dans la mosaïque, surveillance des modifications externes). Utiliser dès qu'une tâche touche à open_with_default_app_qt.py, open_file_with_default_app, ou _EXECUTABLE_EXTS.
---

# Ouvrir avec l'application par défaut — MosaicView

Quand l'utilisateur double-clique sur une entrée **non-image** de la mosaïque (autre que `.nfo` ou `ComicInfo.xml`, qui ont leurs propres éditeurs intégrés — voir skills `nfo-editor`/`comicinfo-metadata-editor`), MosaicView extrait le fichier de l'archive vers un dossier temporaire et l'ouvre avec l'application Windows associée à son extension (comme un double-clic dans l'Explorateur). Si l'utilisateur modifie et sauvegarde le fichier dans cette application externe, la modification est répercutée dans la mosaïque **sans que l'utilisateur ait besoin de la réimporter manuellement**. Un seul fichier : `modules/qt/open_with_default_app_qt.py`.

## Point d'entrée

`PanelWidget._open_non_image_entry(entry)` (`panel_widget.py:2321`) — appelée au double-clic sur une entrée non-image. Route en 3 branches selon l'extension :
1. `.nfo` → `_open_nfo_for_edit` (skill `nfo-editor`).
2. `ComicInfo.xml` (insensible à la casse) → `_open_comicinfo_for_edit` (skill `comicinfo-metadata-editor`).
3. **Tout le reste** → `open_file_with_default_app(entry, state=self._state, on_modified_callback=..., parent=self)`, le sujet de ce skill.

## Fonction principale — `open_file_with_default_app(entry, state, on_modified_callback, parent)`

1. Lit `entry["bytes"]" — retourne immédiatement (silencieusement) si vide/absent.
2. **Vérifie l'extension contre `_EXECUTABLE_EXTS`** (voir section sécurité ci-dessous) — si elle y figure, affiche un avertissement non-modal et **refuse d'ouvrir le fichier**.
3. Calcule le chemin de destination via `safe_join(get_mosaicview_temp_dir(), orig_name)` (protection Zip Slip — voir skill `save-export` pour l'autre usage de `safe_join`) — si `orig_name` contient un `../` piégé, refuse avec un second avertissement dédié.
4. Crée les sous-dossiers nécessaires (`orig_name` peut contenir des `/`, structure préservée pour éviter les collisions de noms entre fichiers de même nom dans des sous-dossiers différents).
5. Écrit `entry["bytes"]` dans ce fichier temporaire, puis `os.startfile(tmp_path)` — Windows dispatche vers l'application déclarée pour cette extension, exactement comme un double-clic natif dans l'Explorateur.
6. Si `state` et `on_modified_callback` sont fournis (toujours le cas depuis le seul appelant actuel), lance `_start_watch_thread` pour surveiller les modifications externes.

## Surveillance des modifications externes (`_start_watch_thread`)

Un `threading.Thread` daemon (`NonImageFileWatcher`) tourne en arrière-plan :

1. Relève `mtime` initial, boucle avec `time.sleep(_POLL_INTERVAL)` (1 seconde) jusqu'à `_WATCH_TIMEOUT` (3600s = 1 heure, arrêt automatique — pas de surveillance infinie si l'utilisateur laisse l'appli externe ouverte sans jamais sauvegarder).
2. Si `mtime` a changé : attend encore 0.3s (laisse le temps à l'application externe de finir d'écrire le fichier), relit le contenu.
3. **Compare le contenu (MD5, `usedforsecurity=False`) au dernier hash connu, pas le `mtime` seul** — certaines applications touchent le mtime à l'ouverture sans modifier le contenu (Notepad, par exemple, peut réécrire le fichier même sans modification réelle) ; comparer uniquement les hashs évite de déclencher `state.modified = True` pour rien.
4. Si le contenu a effectivement changé : met à jour `current_hash[0]` (liste à un élément, mutable depuis la closure — permet de détecter plusieurs sauvegardes successives dans la même session de surveillance) et appelle `on_modified_callback(new_bytes)`.
5. Si le fichier temporaire est supprimé (`OSError` sur `getmtime`) : arrête la surveillance silencieusement.

`on_modified_callback` n'est **pas** appelé directement pour muter `entry["bytes"]` — c'est l'appelant (`_open_non_image_entry`) qui passe `lambda nb, e=entry: self._non_image_modified.emit(e, nb)`, un **signal Qt**. Émettre un signal Qt depuis un thread Python non-Qt est thread-safe par construction (Qt met la connexion en file dans la boucle d'événements du thread principal) — c'est le mécanisme qui permet à ce thread de surveillance de modifier `entry["bytes"]`/`state.modified` en toute sécurité malgré qu'il tourne hors du thread Qt principal.

## Sécurité — `_EXECUTABLE_EXTS`

Liste blanche… en réalité liste **noire** très large (frozenset d'une centaine d'extensions) d'extensions que `os.startfile()` **exécuterait** plutôt que d'afficher — l'utilisateur double-cliquant sur un fichier dans la mosaïque s'attend à le *voir*, pas à *lancer* un programme. Catégories couvertes (voir commentaires inline dans le code, très détaillés) :
- Exécutables/scripts classiques (`.exe`, `.bat`, `.vbs`, `.js`, `.ps1`...).
- Raccourcis et fichiers de recherche/paramètres Windows pouvant exécuter des commandes ou divulguer un hash NTLM via chemin UNC (`.lnk`, `.url`, `.scf`, `.settingcontent-ms`, `.library-ms`...).
- Documents Office à macros **et** formats modernes sans macros (`.docx` etc.) — bloqués par principe, un document bureautique n'ayant rien à faire dans une archive de BD, avec mention explicite de la vulnérabilité Follina (2022) comme précédent.
- Images disque (`.iso`, `.img`, `.vhd`) montées automatiquement par Windows au double-clic.
- Scripts d'interpréteurs tiers (AutoHotkey, AutoIt, Perl, Ruby, Tcl).

Cette liste est **le principal risque de sécurité de ce fichier** — c'est une liste noire, pas une liste blanche, donc intrinsèquement incomplète face à de nouvelles extensions dangereuses ; elle doit être maintenue à jour si Windows/une application tierce introduit un nouveau type de fichier auto-exécutable.

## Comment modifier

- **Ajouter une extension à bloquer** : `_EXECUTABLE_EXTS` (frozenset) — respecter le style de commentaire du fichier (grouper par famille avec une explication du vecteur de risque, pas juste ajouter l'extension nue).
- **Changer l'intervalle/la durée de surveillance** : `_POLL_INTERVAL` (1.0s) / `_WATCH_TIMEOUT` (3600s), constantes en tête de fichier.
- **Changer le comportement en cas de timeout de surveillance** : actuellement silencieux (la boucle se termine, plus aucune modification externe ne sera détectée après 1h) — pas de notification à l'utilisateur ; à ajouter dans `_watch()` si un jour souhaité.
- **Ajouter un nouveau type de fichier avec éditeur intégré** (comme `.nfo`/`ComicInfo.xml`) : modifier `_open_non_image_entry` (`panel_widget.py`) pour intercepter cette extension **avant** qu'elle n'atteigne `open_file_with_default_app` — ce fichier lui-même n'a pas connaissance des cas spéciaux, c'est l'appelant qui aiguille.

## Pièges connus

- **`os.startfile()` peut échouer silencieusement** — aucun `try/except` autour de l'appel ; si aucune application n'est associée à l'extension, Windows affichera sa propre boîte de dialogue "Comment voulez-vous ouvrir ce fichier ?", pas une erreur MosaicView.
- **Le watcher continue de tourner après fermeture du comic** — rien dans ce fichier n'arrête le thread si l'utilisateur ferme l'archive ou l'application avant l'expiration du timeout ; il se contentera de découvrir que le fichier temporaire a disparu (`OSError`) au prochain poll et s'arrêtera de lui-même à ce moment-là, pas immédiatement.
- **Un seul thread de surveillance par ouverture, pas par entrée** — si l'utilisateur double-clique deux fois sur la même entrée (deux instances de l'app externe ouvertes sur le même fichier temporaire), deux threads `_watch()` tournent en parallèle sur le même `tmp_path`, chacun avec son propre `current_hash` local — pas de coordination entre eux, mais pas de risque de corruption non plus (les deux liraient/compareraient le même contenu final).
- **La comparaison MD5 n'est pas cryptographique** (`usedforsecurity=False`, explicite) — c'est volontaire et correct ici : le but est de détecter un changement de contenu, pas de résister à une collision volontaire.

## Références croisées

- `temp-files` — `get_mosaicview_temp_dir()`, dossier de destination de l'extraction ; le fichier temporaire créé ici est couvert par le nettoyage périodique général, pas par une suppression immédiate.
- `save-export` — autre usage de `safe_join()` pour la même protection Zip Slip, côté export vers un dossier.
- `nfo-editor` / `comicinfo-metadata-editor` — les deux cas spéciaux interceptés en amont par `_open_non_image_entry`, qui ne passent jamais par ce fichier.
- `undo-redo` — `state.modified = True` posé indirectement via le signal `_non_image_modified` quand une modification externe est détectée (à vérifier côté `panel_widget.py` si une tâche touche à l'intégration undo de ce flux, non détaillée ici).
