---
name: pdf-loading
description: Localiser ou modifier le chargement de fichiers PDF dans MosaicView (ouverture en pages images, fusion dans un comic ouvert, déverrouillage owner-password, process séparé). Utiliser dès qu'une tâche touche à pdf_loading_qt.py, pdf_unlock_qt.py, ou à l'ouverture d'un .pdf.
---

# Chargement PDF — MosaicView

Ouverture d'un PDF comme une archive (chaque page devient une image de la mosaïque), import/fusion de pages PDF dans un comic déjà ouvert, et déverrouillage des PDF protégés par mot de passe propriétaire ("owner password"). Deux fichiers : `modules/qt/pdf_loading_qt.py` (chargement, l'essentiel du poids) et `modules/qt/pdf_unlock_qt.py` (déverrouillage, dépend du premier pour `_MsgDialog`).

Distinct du reste du chargement d'archives (CBZ/CBR/CB7/CBT/EPUB, voir skill `archive-image-loading`) : le PDF n'est **jamais** ouvert dans le process Qt principal, contrairement à tous les autres formats — architecture multiprocess dédiée, décrite ci-dessous.

## Bibliothèque et disponibilité

`PDF_AVAILABLE` (booléen module-level, `pdf_loading_qt.py`) est déterminé par `try: import fitz except ImportError: PDF_AVAILABLE = False` — PyMuPDF est optionnel. Si absent, `PdfLoader.load()` affiche directement `messages.errors.pymupdf_not_installed` sans tenter quoi que ce soit. Même garde dans `pdf_unlock_qt.py` (`show_pdf_unlock_dialog`/`_save_unlocked` ont leur propre `try: import fitz except ImportError: return`, silencieux).

## Pourquoi un process séparé (et deux, pas un)

Aucun PDF n'est jamais ouvert avec `fitz` dans le process Qt principal — tout passe par un `multiprocessing.Process` (contexte `'spawn'`), pour isoler un crash éventuel de la lib native `fitz`/MuPDF du process UI (un PDF malformé qui ferait planter `fitz` en C tuerait tout le process Python s'il tournait dans le même process que Qt).

Deux singletons de process distincts, tous deux exécutant la même fonction `_pdf_persistent_process` :
- **Process de chargement principal** (`_warm_process`/`_ensure_warm_process`) — pour ouvrir un nouveau PDF en remplacement du comic actuel.
- **Process de merge** (`_merge_process`/`_ensure_merge_process`) — pour fusionner des pages PDF dans un comic déjà ouvert, sans bloquer/interférer avec un chargement principal en cours.

`warmup_pdf_process()` est appelée au démarrage de l'app pour préchauffer le process de chargement (pas le process merge, lancé à la demande) — évite le coût de démarrage (`spawn` + réimport de `fitz`) au moment où l'utilisateur ouvre effectivement un PDF. `shutdown_pdf_process()` doit être appelée à la fermeture de l'app : envoie `('quit',)` aux deux, ferme les pipes, `terminate()` en dernier recours si `join(timeout=2)` échoue.

## Protocole IPC

- **`in_queue`** (`multiprocessing.Queue`) — commandes envoyées au process, tuples `(kind, *args)`.
- **`out_conn`/`out_send`** (`Pipe(duplex=False)`, pas une seconde Queue) — résultats renvoyés, plus rapide pour le flux de données image page par page.

Commandes acceptées par `_pdf_persistent_process` :

| Commande | Usage | Réponses possibles |
|---|---|---|
| `('preopen', filepath)` | Ouvre le PDF pendant que l'utilisateur choisit le DPI | `preopen_ok` / `password_error` / `error` |
| `('run_opened', dpi)` | Convertit le doc déjà pré-ouvert | `total`, `page`×N, `progress`×N, `done` |
| `('run', filepath, dpi)` | Ouvre + convertit en une fois — **fallback** si le process a redémarré entre preopen et run_opened | idem `run_opened` |
| `('run_merge', filepath, dpi, merge_prefix)` | Fusion dans un comic ouvert | `merge_page` au lieu de `page`, sinon identique |
| `('batch_open', filepath)` | Pré-scan pour la bibliothèque/batch (voir skill `batch-pdf-convert`) | `batch_ready` (total, ratios largeur/hauteur, vignette page 0, is_owner) |
| `('batch_convert', filenames_list)` | Conversion complète du doc déjà ouvert par `batch_open` | `batch_page`×N, `done` |
| `('discard',)` | Annule un preopen sans conversion (dialogue DPI annulé) | — |
| `('quit',)` | Arrête le process | — |

Le `doc` PyMuPDF pré-ouvert est gardé dans une variable `nonlocal` **à l'intérieur du process**, entre un `preopen` et le `run_opened`/`discard` qui suit — c'est ce qui permet au parsing PDF de démarrer avant même que l'utilisateur ait choisi le DPI.

## Détection DPI intelligente (DPI = 0, "sans modification")

Quand l'utilisateur choisit `dialogs.pdf.dpi_original` (DPI=0) plutôt qu'une valeur fixe (90/150/200/300/600), chaque page est traitée individuellement dans `_convert`/`_convert_merge`/`batch_convert` (logique dupliquée 3 fois, voir "Pièges") :

1. `page.get_images(full=True)` liste les images incluses ; pour chacune, DPI réel = dimensions image ÷ dimensions page en pouces (`img_width / (page_rect.width / 72.0)`), on garde le maximum (`max_dpi`) et le xref de la meilleure image (`best_xref`).
2. Si la page contient du texte (`page.get_text().strip()`) : DPI minimum forcé à **300** (lisibilité) même si `max_dpi` est plus bas. Sinon : `max_dpi` tel quel, ou **72** si aucune image sur la page.
3. Plafond `min(detected_dpi, 2400)`.
4. **Extraction brute** (`use_raw`) : si la page ne contient **qu'une seule image** ET (pas de texte OU `max_dpi >= 300`), extrait directement les bytes originaux via `doc.extract_image(best_xref)` (formats `jpeg`/`jpg`/`png`/`webp` acceptés) au lieu de rasteriser — évite une recompression/perte de qualité inutile quand la page est essentiellement l'image telle quelle.
5. Sinon (page multi-images, ou texte à haute résolution, ou dpi fixe demandé) : rasterisation via `page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))` puis conversion PIL → JPEG qualité 100, `optimize=True`.

`gc.collect()` tous les 10 pages pendant la conversion — mitigation mémoire pour de gros PDF (des centaines de pages rasterisées peuvent accumuler beaucoup de buffers avant que Python ne les libère spontanément).

## Flux de chargement principal (`PdfLoader.load`)

1. `_ensure_warm_process()` puis envoi immédiat de `('preopen', filepath)` — **avant** d'afficher `DpiDialog`. Le parsing du PDF (juste `fitz.open()`, pas la conversion) se fait donc pendant que l'utilisateur regarde le dialogue DPI, pas après son clic OK — gain de perception de vitesse.
2. Un thread `_drain_preopen` (pas un `QThread`, un `threading.Thread` brut) vide le pipe pendant que le dialogue est affiché — sert à "chauffer" le canal IPC : sans ce thread, le premier `recv()` sur `_warm_out_conn` peut prendre 1-2 secondes sur Windows le temps que le pipe multiprocessing s'établisse réellement, ce qui retarderait visiblement le premier retour de progression.
3. Au clic OK (`_on_dpi_chosen`) : si `_preopen_result[0]` contient déjà `password_error`/`empty_pdf`/`error`, affiche directement le message correspondant **sans lancer `PdfLoadWorker`** (`_MsgDialog` non-modal). Sinon lance le worker.
4. `PdfLoadWorker` (QThread) envoie `('run_opened', dpi)`, boucle sur `_warm_out_conn.poll(0.05)`/`.recv()`, traduit chaque message du pipe en signal Qt (`progress`, `finished`, `error`, `password_error`, `empty_pdf`, `cancelled`).
   - **Fallback automatique** : si le process répond `('error', 'No document pre-opened')` (le process préchauffé est mort et a été redémarré entre le preopen et maintenant), le worker renvoie automatiquement `('run', filepath, dpi)` une seule fois (`_preopen_fallback_sent`, garde anti-boucle) — l'utilisateur ne voit jamais cette resynchronisation.
5. Annulation (`PdfLoader.cancel()`/`PdfLoadWorker.stop()`) : positionne `threading.Event`, mais surtout **tue et jette le process** (`_kill_warm_process`, `terminate()`) — pas d'arrêt propre possible pendant une conversion en cours (`fitz` bloque le thread Python du process). Le prochain PDF ouvert redémarrera un process depuis zéro (`_ensure_warm_process` le détecte via `is_alive()`).

Chaque page reçue (`('page', page_num, img_data, used_dpi)`) devient une entrée standard via `create_entry()` (voir skill `archive-image-loading`) avec `entry["source"] = "pdf"` et `entry["dpi"] = used_dpi`, puis `build_qimage_for_entry(entry)` est appelée **immédiatement** (pas de lazy-loading pour les pages PDF, contrairement au chargement d'archive classique).

## Import/fusion dans un comic ouvert (`import_and_merge_pdf`)

Fonction autonome (pas une méthode de `PdfLoader`), pilote `PdfMergeWorker` via le process merge dédié :
- Préfixe chaque fichier `NEW{state.merge_counter:02d}-page_XXXX.jpg` (compteur incrémenté à chaque merge, visuellement distinct des pages d'origine).
- `entry["source_archive"] = <nom du PDF>` sur chaque nouvelle entrée.
- Tri par nom naturel (`archive_loader._natural_sort_key`) après extension de `state.images_data` — les nouvelles pages s'intercalent selon leur nom, pas forcément à la fin.
- Overlay de progression + bouton Annuler construits à la main ici (pas de classe dédiée comme `PdfLoader`), suit le même pattern `_show_canvas_text`/`_show_cancel_item` (voir skill `canvas-overlay-progress`).

## Déverrouillage owner-password (`pdf_unlock_qt.py`)

Distinct de la protection par **mot de passe utilisateur** (`doc.needs_pass`, qui bloque complètement l'ouverture — `messages.errors.pdf_password_required`, traité en amont dans le protocole IPC). Un PDF **owner-protected** (`doc.authenticate("") == 2`) s'ouvre et se lit normalement — MosaicView le convertit sans problème — mais porte des restrictions (impression, copie...) qu'un lecteur PDF respectueux honorerait.

- Après un chargement réussi (`PdfLoader._on_finished`) ou un merge réussi (`import_and_merge_pdf.on_finished`), si `is_owner_protected` est vrai : `show_pdf_unlock_dialog(filepath, parent)` propose `PdfUnlockDialog` (Oui/Non, non-modal).
- Si Oui → `_save_unlocked()` : réouvre le PDF **original** (pas le comic déjà converti) avec `fitz`, `doc.authenticate("")`, puis `doc.save(dest_path, encryption=fitz.PDF_ENCRYPT_NONE)` — écrit une copie déchiffrée à côté de l'original, suffixée `_unlocked` (ou `_unlocked_N` en cas de collision de nom).
- Succès → `_PdfUnlockedSuccessDialog` avec lien cliquable vers le dossier de destination (`setup_path_label_context_menu`, voir skill `qt-context-menus`).

## Comment modifier

- **Changer la logique de détection DPI** : la même logique existe **en triple** (`_convert`, `_convert_merge`, `batch_convert` dans `_pdf_persistent_process`) — toute correction (ex. ajuster le seuil 300 DPI pour le texte, ou le plafond 2400) doit être répercutée aux 3 endroits, il n'y a pas de fonction commune.
- **Ajouter une option DPI dans le dialogue** : `DpiDialog._dpi_options` (liste de tuples `(dpi, clé_traduction)`) — ajouter une entrée suffit, le reste du flux (radio button, retraduction, tooltip) suit automatiquement.
- **Changer le timeout de préchauffage du canal IPC** : `_drain_preopen`, le `.poll(5)` (5 secondes) dans la boucle — augmenter si des PDF volumineux mettent plus de temps à répondre au `preopen`.
- **Ajouter une nouvelle commande au process** (ex. extraction de métadonnées PDF) : ajouter une branche `elif kind == 'ma_commande':` dans la boucle principale de `_pdf_persistent_process`, suivant le pattern existant (toujours répondre par au moins un message, même en cas d'erreur — le worker Qt qui écoute reste bloqué sinon).
- **Modifier le comportement d'annulation** : `PdfLoader.cancel()`/`PdfMergeWorker` — se rappeler que l'annulation **tue le process** plutôt que de l'interrompre proprement ; un futur besoin d'annulation "propre" (garder le process vivant) demanderait de repenser `_convert`/`_convert_merge` pour vérifier un flag entre chaque page, actuellement absent.

## Pièges connus

- **Logique de détection DPI dupliquée 3 fois** (`_convert`, `_convert_merge`, `batch_convert`) — voir "Comment modifier". Un bug corrigé dans l'une des trois copies doit être vérifié/répliqué dans les deux autres.
- **`_orphan_workers` (liste module-level) retient les workers annulés** — `PdfLoader.cancel()` détache le worker (`setParent(None)`) et le pousse dans `_orphan_workers` jusqu'à ce qu'il émette lui-même son signal `finished`/`cancelled`, seul moment où `deleteLater()` est appelé. Sans cette liste, le `QThread` pourrait être détruit par le GC Python pendant que son thread C++ tourne encore (même risque documenté dans le skill `project_qthread_lifecycle`).
- **Le fallback `run` complet peut se déclencher silencieusement** — si le process préchauffé meurt entre le `preopen` et le clic OK sur le dialogue DPI, l'utilisateur ne voit qu'un léger délai supplémentaire (réouverture complète du PDF), pas d'erreur. Si un bug de lenteur au chargement PDF est signalé de façon intermittente, vérifier si ce chemin de fallback est emprunté.
- **Annulation = destruction du process, pas interruption propre** — un PDF de plusieurs milliers de pages annulé à 50% ne peut pas reprendre : le prochain chargement redémarre un process frais depuis le début.
- **`is_owner_protected` réutilise le mot de passe vide** (`doc.authenticate("")`) — un PDF avec un vrai mot de passe utilisateur non vide ne serait de toute façon jamais arrivé jusqu'ici (`doc.needs_pass` l'aurait bloqué plus tôt dans le protocole).
- **Pas de nettoyage automatique des fichiers `_unlocked`** — écrits à côté de l'original, jamais dans un dossier temporaire ; ne pas les confondre avec les fichiers couverts par le skill `temp-files`.

## Références croisées

- `archive-image-loading` — `create_entry()`, point de passage obligé pour toute page devenant une entrée `images_data` ; le PDF est le seul format dont le chargement passe par un process séparé plutôt que par `ArchiveLoader`/`LoadWorker`.
- `batch-pdf-convert` — réutilise le même process préchauffé et le même protocole IPC (`batch_open`/`batch_convert`/`batch_page`) pour la conversion PDF→CBZ en lot ; toute modification du protocole IPC affecte les deux.
- `canvas-overlay-progress` — overlay de progression + bouton Annuler, utilisé à la fois par `PdfLoader` et `import_and_merge_pdf`.
- `qt-context-menus` — `setup_path_label_context_menu` sur les liens cliquables de `_PdfUnlockedSuccessDialog`/`InfoDialogClickablePath`-like.
- `renumbering` — `state.needs_renumbering = True` déclenché après un chargement PDF réussi, puisque les pages PDF n'ont pas de nom de fichier d'origine porteur d'ordre.
- `temp-files` — sans lien direct : les fichiers `_unlocked` ne sont **pas** gérés par ce mécanisme (voir Pièges), contrairement à la plupart des sorties temporaires de l'appli.
