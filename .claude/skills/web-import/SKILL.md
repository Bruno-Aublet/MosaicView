---
name: web-import
description: Localiser ou modifier l'import d'images depuis le web (saisie manuelle d'URL, drop d'un lien depuis un navigateur, drop d'un fichier .url/.webloc). Utiliser dès qu'une tâche touche à web_import_qt.py, WebImportDialog, ou _resolve_and_download.
---

# Import web — MosaicView

Télécharge une ou plusieurs images depuis Internet (une image directe, ou toutes les images `<img>` d'une page HTML) et les ajoute à la mosaïque. Trois façons d'y arriver, toutes convergent vers les mêmes fonctions de résolution/téléchargement.

Autre source d'image externe avec une architecture proche (worker `QThread`, overlay de progression, ajout à la mosaïque) : voir skill `scan` (numérisation depuis un scanner physique via WIA), qui réutilise directement `_web_import_callbacks()` de `panel_widget.py`.

## Fichier unique — `modules/qt/web_import_qt.py`

Tout vit dans ce seul fichier (~670 lignes) :
- **`WebImportDialog`** — fenêtre de saisie manuelle d'une URL (menu Fichier).
- **`_resolve_and_download()`** — résolution asynchrone d'une URL (image directe ou page HTML), point d'entrée commun au drop de lien et au drop de fichier `.url`/`.webloc`.
- **`_ResolveWorker`** (`QThread`) — télécharge la page/l'image pour déterminer son type (HEAD implicite via GET + `Content-Type`), sans bloquer l'UI.
- **`_DownloadWorker`** (`QThread`) — télécharge effectivement chaque image trouvée.
- **`WebDownloadController`** — orchestre `_DownloadWorker` + l'overlay de progression sur le canvas (texte rouge + bouton Annuler, même pattern visuel que le chargement d'archive).
- **`extract_images_from_html()`** / **`_extract_single_img_src()`** — parsing HTML pour extraire des URLs d'images (page entière vs un seul `<img>` ciblé).
- **`_add_entries_to_mosaic()`** — point de sortie commun : ajoute les entrées téléchargées à `state.images_data` et rafraîchit la mosaïque.

## Les trois points d'entrée

### 1. Saisie manuelle d'URL — `WebImportDialog`

Menu Fichier > "Importer depuis le web" (`menubar_qt.py:90`, aussi menu contextuel canvas `context_menus_qt.py:104`) → `show_web_import_dialog(parent, canvas, callbacks)`.

- Fenêtre non-modale simple : un champ `QLineEdit` + OK/Annuler.
- `_process_url()` (appelé par Entrée ou OK) : normalise l'URL saisie (`https://` ajouté automatiquement si l'utilisateur a tapé juste `example.com`, détecté via présence d'un `.` et absence d'espace), rejette si toujours pas `http(s)://` après normalisation.
- **Différence notable avec les deux autres flux** : ce chemin fait le téléchargement de résolution **de façon synchrone** dans `_process_url()` (un seul `urllib.request.urlopen` bloquant, `timeout=10`) — pas de `_ResolveWorker` ici. La fenêtre se ferme (`self.close()`) **avant** cet appel bloquant, donc l'UI ne gèle que brièvement pendant la résolution, avant que le téléchargement des images individuelles (lui, en thread) ne prenne le relais via `download_and_add_web_images`.
- Résultat `Content-Type` contient `image` → téléchargement direct de cette seule image. Sinon → parsing HTML (`extract_images_from_html`) et téléchargement de toutes les images trouvées.
- Aucune image trouvée ou image invalide → `InfoDialog` (`web.web_copy_paste_message`), pas une erreur bloquante.

### 2. Drop d'un lien depuis un navigateur — `mosaic_canvas.py::dropEvent`

Un lien glissé depuis la barre d'adresse ou une image glissée depuis une page web arrive comme `QMimeData` avec `hasUrls()` mais **sans** `toLocalFile()` (pas un fichier local) — voir skill `drag-and-drop`, flux 4 "drop entrant de fichiers/dossiers/URLs externes", pour le contexte plus large de ce `dropEvent`.

- `mosaic_canvas.py:1893-1921` sépare `local_paths` (fichiers) de `web_urls` (chaînes `http(s)://`).
- Si `web_urls` et pas de `local_paths` : tente d'abord d'extraire l'URL **précise de l'image droppée** via `mime.hasHtml()` — un drop d'image depuis un navigateur porte souvent un fragment `text/html` contenant le `<img src="...">` exact de l'élément survolé, plus fiable que l'URL de la page entière (`web_urls[0]`) qui peut être juste l'URL de la page si le navigateur ne fournit que ça. `_extract_single_img_src(html_fragment, base_url)` (parsing via `lxml` si disponible, sinon `html.parser` stdlib en fallback) résout les URLs relatives via `urljoin`.
- Trouvé → `self._web_import_callback([image_url])` (une seule image ciblée). Sinon → `self._web_import_callback(web_urls)` (les URLs brutes du mime, potentiellement des pages à résoudre).
- `_web_import_callback` est câblé par `PanelWidget` (`panel_widget.py:323`, `self._canvas._web_import_callback = self._handle_dropped_web_urls`) vers `_handle_dropped_web_urls()` (`panel_widget.py:1481`), qui appelle `_resolve_and_download()` pour **chaque** URL de la liste — donc un drop multi-URL (rare mais possible) lance une résolution asynchrone par URL, indépendamment.

### 3. Drop d'un fichier raccourci `.url`/`.webloc`

Distinct du drop de lien : ici c'est un **fichier local** (`toLocalFile()` non vide) glissé depuis l'Explorateur Windows ou le Finder macOS, mais qui contient lui-même une URL à l'intérieur.

- Géré dans `PanelWidget._handle_dropped_paths()` (`panel_widget.py:2082`), **avant** que le fichier ne soit passé au routeur normal de chargement (`handle_dropped_paths`/`_load_files`, voir skill `archive-image-loading`) — ce sont les deux seules extensions interceptées à ce niveau en plus de `.mvdb` (voir skill `library`).
- **`.url`** (raccourci Windows) : parsé via `configparser` (format INI, section `[InternetShortcut]`, clé `URL`). Si absent ou pas `http(s)`, tombe silencieusement dans `regular_paths` (traité comme un fichier normal, qui échouera probablement au chargement).
- **`.webloc`** (raccourci macOS) : parsé via `plistlib` (format XML plist, clé `URL`).
- Dans les deux cas, l'URL extraite est passée à **la même fonction** `_resolve_and_download()` que le drop de lien — aucune différence de traitement une fois l'URL extraite du fichier.

## Résolution asynchrone — `_resolve_and_download()` / `_ResolveWorker`

Point de convergence des flux 2 et 3 (pas du flux 1, qui résout de façon synchrone comme noté plus haut) :

1. **Court-circuit rapide** : `_url_looks_like_image(url)` vérifie l'extension de l'URL (liste `_IMAGE_URL_EXTS`, inclut `.svg` contrairement à `IMAGE_EXTS` du reste du projet — SVG n'est pas rendu par MosaicView mais l'URL est reconnue comme "probablement une image" avant téléchargement). Si oui → téléchargement direct sans passer par le worker de résolution, l'URL de la page est déjà connue comme étant l'image elle-même.
2. Sinon → `_ResolveWorker` (thread) fait un GET complet (pas de vrai HEAD — certains serveurs répondent différemment ou refusent HEAD) avec les en-têtes navigateur complets (`_BROWSER_HEADERS`), lit `Content-Type` :
   - Contient `image` → une seule image (`resolved.emit([url], page_title)`).
   - Sinon → parse le HTML reçu (`extract_images_from_html`) et retourne toutes les URLs d'images trouvées.
3. Erreur réseau : `HTTPError` avec code 403 → traité spécifiquement (`kind="forbidden"`, message dédié `web.web_drop_forbidden_*` — un site avec protection anti-bot type Cloudflare rejette souvent une requête sans les bons en-têtes) ; toute autre erreur → message générique `web.web_drop_error_*`.
4. **Garde-vie du worker** : référence stockée dans `canvas._resolve_workers` (liste, attribut dynamique sur le canvas) jusqu'à la fin du thread — même famille de piège anti-GC documentée pour d'autres workers du projet (voir skill `comicvine-metadata-fetch`, section workers Qt, et `archive-image-loading`, section `_orphan_workers`).

## En-têtes HTTP — pourquoi deux jeux différents

`_BROWSER_HEADERS` (résolution d'URL/page) vs `_IMAGE_HEADERS` (téléchargement d'image déjà identifiée) :
- `_BROWSER_HEADERS` annonce le support AVIF/WebP dans `Accept` — nécessaire pour qu'un serveur retourne du HTML normal plutôt qu'une erreur anti-bot.
- `_IMAGE_HEADERS` **n'annonce pas** AVIF/WebP — certains CDN à négociation de contenu (mentionné : Optimole) reconvertissent l'image servie vers ces formats modernes si le client dit les supporter, alors qu'on veut souvent le format d'origine (ex. JPEG) pour rester cohérent avec ce que l'utilisateur voyait sur la page. Un `User-Agent` seul ne suffit pas à passer certaines protections — d'où l'en-tête complet imitant un vrai navigateur dans les deux cas.

## Téléchargement effectif — `_DownloadWorker`

- Pour chaque URL d'image : téléchargement (`urllib.request`, timeout 10s), validation via PIL (`Image.open` + `img.verify()`) — une URL qui prétendait être une image mais ne l'est pas est silencieusement ignorée (`except: pass`), pas d'erreur bloquante par image individuelle.
- **Correction d'extension** : si le format réel détecté par PIL (`img.format`) diffère de l'extension déduite du nom de fichier dans l'URL, le nom est corrigé (ex. une image servie en `.jpg` mais réellement WebP devient `image.webp`) — même logique de cohérence que la détection de type d'archive (voir skill `archive-image-loading`, `detect_archive_type`), mais ici au niveau image individuelle plutôt qu'archive entière.
- **Nom de fichier** : dérivé du chemin de l'URL (`os.path.basename`) si présent et avec extension, sinon généré (`{page_title}_{idx+1:03d}.jpg`) — `page_title` vient du domaine de la page source (`urlparse(url).netloc`, `www.` retiré).
- **Préfixe `NEW-`** : ajouté si un comic est déjà ouvert (`state.current_file is not None`) OU si `state.images_data` contient déjà des entrées OU si d'autres entrées ont déjà été ajoutées **dans ce même lot** de téléchargement (`new_entries` non vide) — même convention que les autres chemins d'ajout après-coup (voir skill `archive-image-loading`, section chargement d'images isolées).
- `entry["source_archive"] = "web"` — marqueur d'origine, distinct de `"loose"` (fichier local isolé) utilisé ailleurs.
- Chaque entrée passe par `create_entry()` (`entries.py`) comme tout autre chemin de chargement — voir skill `archive-image-loading` pour ce que fait cette fonction (décodage PIL, hash MD5, dimensions, GIF animé...).

## Interaction avec la mosaïque et les panneaux — `_add_entries_to_mosaic()`

Point de sortie commun à `WebDownloadController._on_finished` (flux drop/résolution asynchrone) et à `WebImportDialog._process_url` (flux saisie manuelle) :

1. Si `state.images_data` est vide (aucun comic ouvert), crée un point undo (`save_state()`) **avant** d'ajouter — cohérent avec le pattern des autres chemins d'ajout après-coup (voir skill `archive-image-loading`, `_ImageLoadWorker`/`_start_image_load`).
2. `state.images_data.extend(entries)` puis retri par tri naturel (`_natural_sort_key`, importé depuis `archive_loader.py` — **pas dupliqué**, un seul point de tri naturel dans tout le projet).
3. `state.modified = True`, `state.needs_renumbering = True` si au moins une image est présente (voir skill `renumbering` pour ce que ce flag active/désactive — pas de déclenchement automatique de renumérotation ici, juste l'activation du bouton).
4. `clear_selection()`, `render_mosaic()`, rafraîchissement des boutons/toolbar via les callbacks fournis.

**Dict `callbacks`** (contrat entre ce module et le panneau appelant, voir `PanelWidget._web_import_callbacks()`, `panel_widget.py:1486`) : `state`, `save_state`, `render_mosaic`, `update_button_text`, `update_create_cbz_button`, `clear_selection` — même style de contrat que `_get_batch_callbacks()` documenté dans le skill `batch-processing`, mais plus petit et spécifique à ce module. Toujours passer par cette méthode plutôt que de construire le dict à la main dans un nouveau call-site.

**Chaque panneau (panel1/panel2 en split-view) a son propre `_web_import_callback` et ses propres callbacks** — un import web déclenché sur le panneau 2 n'écrit jamais dans `images_data` du panneau 1 (voir skill `panels`).

## Comment étendre

- **Ajouter une nouvelle source de drop reconnue** (ex. un format de raccourci supplémentaire) : suivre le pattern `.url`/`.webloc` dans `PanelWidget._handle_dropped_paths()` — parser le fichier pour en extraire une URL `http(s)`, puis appeler `_resolve_and_download()` avec cette URL, exactement comme les deux cas existants.
- **Changer la détection "URL qui ressemble à une image"** : uniquement `_url_looks_like_image()`/`_IMAGE_URL_EXTS` — attention, cette liste diffère volontairement de `IMAGE_EXTS` (inclut `.svg`), ne pas les fusionner sans vérifier l'impact.
- **Changer le comportement anti-bot** (en-têtes, gestion 403) : `_BROWSER_HEADERS`/`_classify` dans `_ResolveWorker.run()` — un site qui bloque encore malgré ces en-têtes n'a pas de contournement supplémentaire prévu (pas de rotation de User-Agent, pas de proxy).
- Respecter les 8 règles UI Qt obligatoires du CLAUDE.md pour `WebImportDialog` (non-modale déjà en place, `_wt()` pour le titre déjà en place).

## Pièges connus

- **Ne pas confondre les trois mécanismes de résolution** : saisie manuelle = synchrone dans `_process_url` ; drop de lien/fichier raccourci = asynchrone via `_ResolveWorker`/`_resolve_and_download`. Un bug de blocage UI signalé sur l'un ne se reproduit pas forcément sur l'autre.
- **`canvas._resolve_workers` est un attribut dynamique**, pas déclaré dans `__init__` de `MosaicCanvas` — créé à la volée par `getattr(canvas, '_resolve_workers', [])` au premier appel. Ne pas supposer qu'il existe avant tout appel à `_resolve_and_download`.
- **Le drop de lien depuis un navigateur peut porter plusieurs représentations MIME simultanément** (`hasUrls()` ET `hasHtml()`) — toujours préférer le fragment HTML pour cibler l'image exacte quand disponible, ne pas se contenter de `web_urls[0]` par simplicité si `mime.hasHtml()` est vrai.
- **Un `.url`/`.webloc` sans URL valide à l'intérieur retombe silencieusement dans `regular_paths`** — pas un message d'erreur dédié, le fichier suit le chemin normal de chargement et échouera probablement avec un message générique "format non supporté" (voir skill `archive-image-loading`).
- **Le worker de téléchargement (`_DownloadWorker`) n'émet aucune erreur par image individuelle** — une image qui échoue (404, format invalide) est juste absente du résultat final ; seul `no_images` (aucune image téléchargée sur tout le lot) déclenche un message utilisateur.
