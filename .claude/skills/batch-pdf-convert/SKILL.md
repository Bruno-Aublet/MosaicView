---
name: batch-pdf-convert
description: Localiser ou modifier la conversion par lot PDF→CBZ de MosaicView (process PyMuPDF dédié au lot, détection DPI, owner-password). Utiliser dès qu'une tâche touche à batch_convert_pdf_to_cbz ou au traitement "Conversion en lot de fichiers PDF en CBZ".
---

# Conversion par lot PDF→CBZ — MosaicView

Un des 8 traitements par lot du projet (skill `batch-processing`, **à lire en premier** pour l'architecture commune : pattern confirm/progress/summary, contrat `batch_callbacks`, deux points d'entrée menu/drop). **Le flux le plus structurellement différent des 4 conversions classiques** — pas de `rarfile`/`7z.exe`/`tarfile` en lecture directe, mais un **process Python séparé** dédié au décodage PyMuPDF, communiquant par messages. Dans `modules/qt/batch_dialogs_qt.py:1774-2033`, protocole implémenté dans `modules/qt/pdf_loading_qt.py`.

## Dépendance bloquante — PyMuPDF (`fitz`)

Comme CBR (`rarfile`), bloque avant même le scan de dossier si l'import a échoué : `if not PDF_AVAILABLE: ErrorDialog(...)`, `PDF_AVAILABLE` calculé une fois en tête de `batch_dialogs_qt.py` (`try: import fitz; PDF_AVAILABLE = True except ImportError: PDF_AVAILABLE = False`).

## Architecture — process dédié au lot, pas un thread classique

**Le point le plus important à comprendre avant de toucher à ce flux.** `_run_pdf_conversion` lance bien un `threading.Thread` (`do_conversion`) comme les 3 autres flux classiques — mais ce thread ne décode **aucun** PDF lui-même : il envoie des messages à un **process séparé** (`multiprocessing`, pas `threading`) via une file (`batch_in_q`) et reçoit les réponses via un pipe (`batch_out_conn`), créé et détruit localement dans `do_conversion` via les fabriques de `modules/qt/pdf_loading_qt.py` — **jamais partagé** avec le chargement PDF normal d'un panneau, un autre batch, ou l'import de métadonnées en lot (voir skill `pdf-loading` pour l'architecture complète et le piège de concurrence que ce cloisonnement corrige).

- **`_spawn_pdf_process()`** (`pdf_loading_qt.py`) : crée un process dédié (`multiprocessing.get_context('spawn').Process`, cible `_pdf_persistent_process`, `daemon=True`) au tout début de `do_conversion`, **avant** la boucle sur les fichiers du lot — **réutilisé d'un fichier à l'autre du même lot** (pas relancé à chaque PDF), mais détruit avec `_kill_pdf_process()` dès la fin du lot (dernière itération, ou branche d'erreur). Le coût de démarrage d'un interpréteur Python + import de PyMuPDF n'est donc payé qu'une fois par lot, jamais réutilisé au-delà (contrairement à un ancien mécanisme de process préchauffé partagé, abandonné).
- **`_send(msg)`/`_recv(timeout)`** : wrappers locaux à `do_conversion` autour de `batch_in_q.put(msg)`/`batch_out_conn.poll(timeout) + recv()` — chaque réception a un timeout explicite (30s pour l'ouverture, 120s pour une page) ; un timeout est traité comme une erreur pour ce fichier, pas un blocage indéfini de tout le lot.

## Protocole de messages — 3 étapes par fichier

Chaque tuple envoyé/reçu a son premier élément comme "type de message" (`kind`), géré par une grande boucle `if/elif` dans le process (`pdf_loading_qt.py:611+`, partagée avec les autres modes d'usage du process — `preopen`/`run`/`run_merge` pour le chargement normal, `batch_*` pour ce flux) :

1. **`('batch_open', filepath)` → `('batch_ready', ...)` ou `('error', msg)`** : ouvre le PDF (`fitz.open`), vérifie `doc.needs_pass` — si protégé par mot de passe utilisateur (pas juste owner), répond `('batch_ready', None)` (le `None` en 2ᵉ position signifie "nécessite un mot de passe", testé côté appelant par `batch_ready[1] is None`) et le fichier est **skippé** (`messages.errors.pdf_encrypted_skipped`), pas de tentative de déverrouillage automatique à ce stade. Sinon, authentifie avec mot de passe vide (`doc.authenticate("") == 2` → `is_owner`, un PDF à owner-password seul s'ouvre sans mot de passe mais reste "protégé" au sens propriétaire) et répond `('batch_ready', total_pages, ratios, thumb_bytes, is_owner)` :
   - `ratios` : liste des ratios largeur/hauteur (`page.rect`) de **chaque page**, calculée intégralement avant toute conversion — nécessaire pour la renumérotation automatique (voir section dédiée).
   - `thumb_bytes` : vignette basse résolution de la page 0 (`fitz.Matrix(0.5, 0.5)`, JPEG qualité 80) pour affichage dans `_ProgressDialog`.
2. **`('batch_convert', filenames_list)` → flux de `('batch_page', ...)` puis `('done',)`** : `filenames_list` précalculé côté appelant (voir section nommage) et envoyé **avant** que la conversion page par page ne démarre — le process n'invente jamais de nom de fichier lui-même. Pour chaque page, un message `('batch_page', filename, img_bytes, pct, page_num+1, total_pages)` est renvoyé dès que la page est prête ; l'appelant écrit `img_bytes` dans le CBZ **au fil de l'eau** (dans le `with zipfile.ZipFile(...)`, pas accumulé en mémoire puis écrit à la fin).
3. **`('done',)`** termine la boucle de réception pour ce fichier ; une **`('error', msg)`** à tout moment sort de la boucle avec le fichier marqué en erreur.

## Détection DPI intelligente — dans le process, pas dans `batch_dialogs_qt.py`

**Logique la plus sophistiquée de tout le mécanisme batch**, entièrement dans `pdf_loading_qt.py:701-777` (partagée avec le chargement PDF normal — modifier ce comportement affecte aussi bien le batch que l'ouverture PDF classique) :

- Pour chaque page, inspecte `page.get_images(full=True)` — si une **seule** image occupe la page (`len(image_list) == 1`) et que son DPI apparent (`max(iw/pw, ih/ph)`, comparaison taille pixel réelle vs taille physique de la page en pouces) est suffisant (`max_dpi >= 300` ou la page n'a pas de texte superposé), **extrait l'image source telle quelle** (`doc.extract_image`, formats `jpeg`/`jpg`/`png`/`webp` acceptés) plutôt que de rasteriser la page — évite une recompression avec perte sur un scan déjà en JPEG haute qualité intégré au PDF.
- Sinon (page composée de plusieurs éléments, texte + image, ou image de résolution insuffisante), **rasterise la page entière** (`page.get_pixmap`) à un DPI déterminé dynamiquement : `max(max_dpi, 300)` si la page contient du texte (garantit une lisibilité minimale), sinon `max_dpi` détecté ou `72` par défaut si aucune image trouvée — plafonné à `2400` DPI dans tous les cas (`min(detected_dpi, 2400)`, protection contre un PDF pathologique qui produirait une image démesurée).
- `gc.collect()` tous les 10 pages (plus fréquent que les autres flux, à 20 pages) — cohérent avec le fait que le décodage PDF/rasterisation PyMuPDF est plus gourmand en mémoire pic que la simple relecture d'une image déjà compressée dans une archive.

## Renumérotation automatique — `state.renumber_mode`

**Seul flux batch qui consulte `state.renumber_mode`** (skill `renumbering`) pour déterminer le nommage des pages de sortie :
- Mode `1` (auto) : `callbacks['compute_auto_multipliers'](ratios)` puis `callbacks['generate_auto_filenames'](multipliers, ".jpg")` — utilise les ratios largeur/hauteur reçus dans `batch_ready` pour détecter les pages doubles/triples et leur attribuer un préfixe de tri approprié (même logique que le mode auto en usage normal, skill `renumbering`).
- Sinon : nommage séquentiel simple, `str(i+1).zfill(digits) + ".jpg"`, `digits = max(2, len(str(total_pages)))` (au moins 2 chiffres, plus si le PDF a 100+ pages).

`state` est passé explicitement à `_run_pdf_conversion` (paramètre dédié, pas seulement via le dict `callbacks`) — capturé au moment de `batch_convert_pdf_to_cbz_confirm` (`callbacks.get('state') or _state_module.state`).

## PDF protégés par owner-password — après le résumé

`owner_protected` (liste des chemins) accumulée pendant la boucle. **Ces fichiers ne sont jamais supprimés** après conversion réussie (`if pdf_path not in owner_protected: ... os.remove/safe_delete_file`) — contrairement aux autres fichiers du lot, une source owner-protected reste intacte sur disque même en cas de succès, cohérent avec la prudence de ne pas supprimer un fichier dont on n'a pas pleinement validé l'accès. `_PdfSummaryDialog._on_ok` (`batch_dialogs_qt.py:579`) propose, **après fermeture du résumé**, un déverrouillage en lot de ces fichiers via `pdf_unlock_qt.show_batch_pdf_unlock_dialog` (fenêtre séparée, hors périmètre de ce skill) — appelé dans un `try/except Exception: pass` silencieux, un échec d'ouverture de cette fenêtre secondaire n'interrompt jamais le flux principal déjà terminé.

## Écriture du CBZ

Contrairement aux 3 autres flux, **pas de normalisation de mode couleur** ici (pas de bloc `CMYK`/`YCbCr`/`I`/`F` → `RGB`) — les bytes reçus du process (`raw_image_bytes` extrait directement, ou JPEG fraîchement encodé par `img.save(..., format='JPEG', quality=100)`) sont toujours dans un format d'écriture direct valide, PyMuPDF ne produisant jamais un mode PIL exotique côté process. Même réglage de compression utilisateur (`zip_compression_kwargs`, skill `zip-compression`) que les autres flux.

## Log — uniquement les erreurs, pas de section renommage

`Log_pdftocbz_{timestamp}.txt` écrit seulement si `conversion_errors` est non vide (pas de condition `renamed_entries` — PDF n'a aucun mécanisme de "fichier mal nommé" comme CBR/CB7/CBT, un `.pdf` est toujours traité comme tel, jamais redirigé vers un autre format).

## Comment étendre

- **Changer la logique de détection DPI** : uniquement dans `pdf_loading_qt.py`, bloc `elif kind == 'batch_convert':` — cette branche du protocole est spécifique au batch (distincte de `run`/`run_opened`/`run_merge` utilisées par le chargement normal), mais la logique de détection elle-même est dupliquée dans les autres branches (voir skill `pdf-loading`, section "logique de détection DPI dupliquée 3 fois") : toute correction doit être répercutée aux 3 endroits.
- **Ajouter un timeout configurable** (actuellement 30s ouverture / 120s par page, en dur) : `_recv(timeout=...)` dans `do_conversion`, deux appels distincts à ajuster séparément.
- **Ne pas copier ce fichier comme modèle** pour un nouveau flux de conversion simple — voir skill `batch-processing`, qui recommande explicitement CBT (skill `batch-cbt-convert`) comme base, ce flux PDF étant une exception structurelle du projet.

## Pièges connus

- **Le thread `do_conversion` ne décode rien lui-même** — toute la charge PyMuPDF est déportée dans le process séparé ; un breakpoint/print dans `do_conversion` ne verra jamais la logique de rasterisation réelle, qui vit dans `pdf_loading_qt.py`.
- **Le process est créé au tout début de `do_conversion` et détruit à la fin (succès ou erreur)** — ne jamais le remplacer par un appel aux fabriques utilisées par un panneau (`PdfLoader`) ou l'import de métadonnées en lot : chacun doit garder son propre process, jamais un singleton partagé (voir skill `pdf-loading` pour le bug "pipe broken" que ce cloisonnement corrige).
- **PDF protégé par owner-password jamais supprimé après conversion**, contrairement au comportement par défaut des autres fichiers du lot — le proposer en déverrouillage seulement après coup, jamais pendant le traitement principal.
- **Pas de normalisation de mode couleur à l'écriture** — contrairement aux 3 autres flux classiques, cohérent avec le fait que le process ne produit jamais un mode PIL non standard.
- **`gc.collect()` tous les 10 pages ici, contre 20 pour les autres flux** — plus fréquent, à ne pas harmoniser par erreur en copiant depuis CBR/CB7/CBT.
- **Aucun mécanisme de redirection "fichier mal nommé"** — contrairement aux 3 autres flux, PDF n'a pas de `detect_archive_type`/renommage, donc pas de compteurs `renamed_*` dans son résumé.

## Références croisées

- `batch-processing` — architecture commune (pattern confirm/progress/summary/thread, contrat de callbacks, points d'entrée menu/drop) ; à lire avant ce skill-ci ; identifie déjà ce flux comme structurellement à part.
- `pdf-loading` — le chargement PDF **normal** (hors batch) utilise le même protocole de messages (`preopen`/`run`/`run_merge`/`batch_open`/`batch_convert`) et les mêmes fabriques de process (`_spawn_pdf_process`/`_kill_pdf_process`), mais avec son propre process dédié à chaque panneau — jamais celui du batch. Voir ce skill pour l'architecture complète (détection DPI, déverrouillage owner-password, le piège de concurrence entre appelants).
- `renumbering` — `compute_auto_multipliers`/`generate_auto_filenames`, consultés ici via `state.renumber_mode` pour le nommage des pages en mode auto.
- `zip-compression` — réglage appliqué à l'écriture du CBZ de sortie.
- `temp-files` — emplacement du log d'erreurs.
- `batch-cbt-convert` — modèle recommandé pour un nouveau flux simple, par contraste avec la complexité structurelle de celui-ci.
