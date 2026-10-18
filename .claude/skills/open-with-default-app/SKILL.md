---
name: open-with-default-app
description: Localiser ou modifier l'ouverture d'un fichier non-image avec l'application Windows par défaut (double-clic dans la mosaïque, surveillance des modifications externes). Utiliser dès qu'une tâche touche à open_with_default_app_qt.py, open_file_with_default_app, openable_temp_name, ou _OPENABLE_EXTS (liste blanche des types ouverts).
---

# Ouvrir avec l'application par défaut — MosaicView

Quand l'utilisateur double-clique sur une entrée **non-image** de la mosaïque (autre que `.nfo` ou le ComicInfo.xml du document, qui ont leurs propres éditeurs intégrés — voir skills `nfo-editor`/`comicinfo-metadata-editor` ; un `ComicInfo_0002.xml` ou un ComicInfo.xml rangé dans un dossier est un simple fichier `.xml`, ouvert ici), MosaicView extrait le fichier de l'archive vers un dossier temporaire et l'ouvre avec l'application Windows associée à son extension (comme un double-clic dans l'Explorateur). Si l'utilisateur modifie et sauvegarde le fichier dans cette application externe, la modification est répercutée dans la mosaïque **sans que l'utilisateur ait besoin de la réimporter manuellement**. Un seul fichier : `modules/qt/open_with_default_app_qt.py`.

## Point d'entrée

`PanelWidget._open_non_image_entry(entry)` (`panel_widget.py`) — appelée au double-clic sur une entrée non-image. Route en 3 branches selon l'extension :
1. `.nfo` → `_open_nfo_for_edit` (skill `nfo-editor`).
2. `ComicInfo.xml` (insensible à la casse) → `_open_comicinfo_for_edit` (skill `comicinfo-metadata-editor`).
3. **Tout le reste** → `open_file_with_default_app(entry, state=self._state, on_modified_callback=..., parent=self)`, le sujet de ce skill.

## Fonction principale — `open_file_with_default_app(entry, state, on_modified_callback, parent)`

1. Lit `entry["bytes"]" — retourne immédiatement (silencieusement) si vide/absent.
2. **`openable_temp_name(orig_name)`** (voir section sécurité ci-dessous) : nom réellement écrit, ou `None` si le type n'est pas dans la liste blanche → `_warn_blocked_file(parent)` (avertissement non modal, clé `messages.warnings.file_open_blocked`) et **refus d'ouvrir le fichier**.
3. Calcule le chemin de destination via `safe_join(get_mosaicview_temp_dir(), nom_écrit)` (défense en profondeur : `windows_safe_relpath` a déjà retiré les `..`) — si `safe_join` refuse, second avertissement dédié (`_warn_unsafe_path`).
4. Crée les sous-dossiers nécessaires (le nom peut contenir des `/`, structure préservée pour éviter les collisions de noms entre fichiers de même nom dans des sous-dossiers différents).
5. Écrit `entry["bytes"]` dans ce fichier temporaire, puis `os.startfile(tmp_path)` — Windows dispatche vers l'application déclarée pour cette extension, exactement comme un double-clic natif dans l'Explorateur.
6. Si `state` et `on_modified_callback` sont fournis (toujours le cas depuis le seul appelant actuel), lance `_start_watch_thread` pour surveiller les modifications externes.

## Surveillance des modifications externes (`_start_watch_thread`)

Un `threading.Thread` daemon (`NonImageFileWatcher`) tourne en arrière-plan :

1. Relève `mtime` initial, boucle avec `stop.wait(_POLL_INTERVAL)` (1 seconde) jusqu'à `_WATCH_TIMEOUT` (3600s = 1 heure, arrêt automatique — pas de surveillance infinie si l'utilisateur laisse l'appli externe ouverte sans jamais sauvegarder). Elle s'arrête aussi dès que `state.doc_generation` diffère de sa valeur au lancement (document fermé ou remplacé : la page surveillée n'existe plus), et quand le même fichier temporaire est rouvert : `_watch_stops` (dict `tmp_path → threading.Event`, sous `_watch_lock`) pose l'Event de la surveillance précédente, sinon deux threads signaleraient chaque modification deux fois. Le thread retire son entrée du dict à sa fin si elle est encore la sienne.
2. Si `mtime` a changé : attend encore 0.3s (laisse le temps à l'application externe de finir d'écrire le fichier), relit le contenu.
3. **Compare le contenu (MD5, `usedforsecurity=False`) au dernier hash connu, pas le `mtime` seul** — certaines applications touchent le mtime à l'ouverture sans modifier le contenu (Notepad, par exemple, peut réécrire le fichier même sans modification réelle) ; comparer uniquement les hashs évite de déclencher `state.modified = True` pour rien.
4. Si le contenu a effectivement changé : met à jour `current_hash[0]` (liste à un élément, mutable depuis la closure — permet de détecter plusieurs sauvegardes successives dans la même session de surveillance) et appelle `on_modified_callback(new_bytes)`.
5. Si le fichier temporaire est supprimé (`OSError` sur `getmtime`) : arrête la surveillance silencieusement.

`on_modified_callback` n'est **pas** appelé directement pour muter `entry["bytes"]` — c'est l'appelant (`_open_non_image_entry`) qui passe `lambda nb, e=entry: self._non_image_modified.emit(e, nb)`, un **signal Qt**. Émettre un signal Qt depuis un thread Python non-Qt est thread-safe par construction (Qt met la connexion en file dans la boucle d'événements du thread principal) — c'est le mécanisme qui permet à ce thread de surveillance de modifier `entry["bytes"]`/`state.modified` en toute sécurité malgré qu'il tourne hors du thread Qt principal.

Le slot `PanelWidget._on_non_image_file_modified(entry, new_bytes)` passe la modification à `_apply_non_image_modification` par `state.run_when_idle` (appliquée à la fin d'une opération en cours sur le document, skill `undo-redo`) ; celle-ci vérifie d'abord que `entry` appartient toujours à `self._state.images_data` (par identité, `is`) et ignore la modification sinon. La surveillance s'arrête à la fermeture du fichier (`doc_generation`), mais au plus une seconde plus tard : une modification relevée juste avant peut encore arriver. Sans cette vérification, enregistrer le fichier dans l'application externe après avoir fermé le comic (et en avoir ouvert un autre dans le même panneau) marquerait ce nouveau comic comme modifié et y ajouterait deux points d'annulation parasites (les deux `save_state(force=True)` du slot).

## Sécurité — liste blanche `_OPENABLE_EXTS` et `openable_temp_name`

L'utilisateur qui double-clique sur un fichier de la mosaïque s'attend à le *voir*, pas à *lancer* un programme contenu dans une archive téléchargée. Seules les extensions de **`_OPENABLE_EXTS`** (frozenset en tête de fichier) sont ouvertes : texte (`.txt`, `.md`, `.xml`, `.json`, `.csv`, `.log`, `.srt`…), documents (`.pdf`, `.epub`), sons et vidéos, images que MosaicView n'affiche pas (`.psd`, `.heic`, `.jxl`…). **Tout autre type est refusé.**

Une liste blanche et non une liste de types dangereux : une telle liste est toujours incomplète (un `.pyc` est exécuté si le lanceur Python est associé, un `.rtf` sert de vecteur d'attaque contre Office, un `.svg` ou un `.html` exécute un script dans le navigateur), et ce qui s'exécute dépend des logiciels installés sur chaque machine.

**`openable_temp_name(orig_name)`** juge le type sur le **nom réellement écrit**, `utils.windows_safe_relpath(orig_name)` (skill `save-export`, section noms Windows) : Windows retire le point ou l'espace final d'un nom et lit `nom::$DATA` comme le fichier lui-même. Pour `evil.exe.`, `os.path.splitext` rend l'extension `.` : jugé sur le nom d'origine, le fichier passerait le filtre, serait écrit `evil.exe` puis lancé par `os.startfile`. Jamais de contrôle d'extension sur `orig_name` brut.

Le message de refus (`messages.warnings.file_open_blocked`, titre « Ouverture bloquée ») ne cite pas le type du fichier : « Ce fichier n'a pas été ouvert : par sécurité, MosaicView n'ouvre que certains types de fichiers. » Il reste juste pour tout type refusé, y compris un fichier sans extension. Ne pas y réintroduire de liste de types ni de mode d'emploi (choix de l'utilisateur).

## Comment modifier

- **Autoriser un nouveau type** : l'ajouter à `_OPENABLE_EXTS`, seulement s'il ne peut rien exécuter avec ses applications usuelles (pas de macros, pas de scripts, pas de liens vers d'autres fichiers ou chemins réseau). Mettre à jour `tests/test_untrusted_names.py` (noms acceptés et refusés).
- **Changer l'intervalle/la durée de surveillance** : `_POLL_INTERVAL` (1.0s) / `_WATCH_TIMEOUT` (3600s), constantes en tête de fichier.
- **Changer le comportement en cas de timeout de surveillance** : actuellement silencieux (la boucle se termine, plus aucune modification externe ne sera détectée après 1h) — pas de notification à l'utilisateur ; à ajouter dans `_watch()` si un jour souhaité.
- **Ajouter un nouveau type de fichier avec éditeur intégré** (comme `.nfo`/`ComicInfo.xml`) : modifier `_open_non_image_entry` (`panel_widget.py`) pour intercepter cette extension **avant** qu'elle n'atteigne `open_file_with_default_app` — ce fichier lui-même n'a pas connaissance des cas spéciaux, c'est l'appelant qui aiguille.

## Pièges connus

- **`os.startfile()` peut échouer silencieusement** — aucun `try/except` autour de l'appel ; si aucune application n'est associée à l'extension, Windows affichera sa propre boîte de dialogue "Comment voulez-vous ouvrir ce fichier ?", pas une erreur MosaicView.
- **Arrêt de la surveillance pas immédiat** — à la fermeture du comic, le thread ne s'arrête qu'à son prochain tour (au plus `_POLL_INTERVAL` plus tard, en constatant que `doc_generation` a changé) : d'où la vérification d'appartenance faite par `_apply_non_image_modification` (section précédente).
- **Une seule surveillance par fichier temporaire** — rouvrir la même entrée (même `tmp_path`) arrête la surveillance précédente par `_watch_stops` ; deux surveillances du même fichier signaleraient chaque modification deux fois.
- **Deux comics peuvent avoir un fichier non-image de même nom** : ils partagent alors le même `tmp_path` dans `MosaicViewTemp`, et la seconde ouverture remplace le fichier et la surveillance de la première.
- **La comparaison MD5 n'est pas cryptographique** (`usedforsecurity=False`, explicite) — c'est volontaire et correct ici : le but est de détecter un changement de contenu, pas de résister à une collision volontaire.

## Références croisées

- `temp-files` — `get_mosaicview_temp_dir()`, dossier de destination de l'extraction ; le fichier temporaire créé ici est couvert par le nettoyage périodique général, pas par une suppression immédiate.
- `save-export` — `windows_safe_relpath`/`safe_join` (`utils.py`), mêmes règles de noms côté export vers un dossier.
- `nfo-editor` / `comicinfo-metadata-editor` — les deux cas spéciaux interceptés en amont par `_open_non_image_entry`, qui ne passent jamais par ce fichier.
- `undo-redo` — `state.modified = True` posé indirectement via le signal `_non_image_modified` quand une modification externe est détectée (à vérifier côté `panel_widget.py` si une tâche touche à l'intégration undo de ce flux, non détaillée ici).
