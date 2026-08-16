---
name: web-import
description: Localiser ou modifier l'import d'images depuis le web (saisie manuelle d'URL, drop d'un lien depuis un navigateur, drop d'un fichier .url/.webloc). Utiliser dès qu'une tâche touche à web_import_qt.py, WebImportDialog, ou _resolve_and_download.
---

# Import web — MosaicView

Télécharge une ou plusieurs images depuis Internet (une image directe, ou toutes les images d'une page HTML — statiques ou générées par JavaScript) et les ajoute à la mosaïque. Trois façons d'y arriver, toutes convergent vers les mêmes fonctions de résolution/téléchargement.

Autre source d'image externe avec une architecture proche (worker `QThread`, overlay de progression, ajout à la mosaïque) : voir skill `scan` (numérisation depuis un scanner physique via WIA), qui réutilise directement `_web_import_callbacks()` de `panel_widget.py`.

## Fichier unique — `modules/qt/web_import_qt.py`

Tout vit dans ce seul fichier (~880 lignes) :
- **`WebImportDialog`** — fenêtre de saisie manuelle d'une URL (menu Fichier).
- **`_resolve_and_download()`** — résolution asynchrone d'une URL (image directe ou page HTML), point d'entrée commun aux trois flux (saisie manuelle incluse — voir plus bas).
- **`_ResolveWorker`** (`QThread`) — télécharge la page/l'image pour déterminer son type (GET + `Content-Type`), extrait les URLs d'images si c'est une page HTML, sans bloquer l'UI.
- **`_DownloadWorker`** (`QThread`) — télécharge effectivement chaque image trouvée, avec retry sur échec réseau transitoire.
- **`WebDownloadController`** — orchestre `_DownloadWorker` + l'overlay de progression sur le canvas (texte rouge + bouton Annuler, même pattern visuel que le chargement d'archive).
- **`extract_images_from_html()`** — parsing HTML statique pour extraire les `<img src="...">` déjà présents dans le HTML brut.
- **`extract_images_from_js_loops()`** — extraction complémentaire des images générées par JavaScript via des boucles (voir section dédiée plus bas).
- **`_extract_single_img_src()`** — extraction ciblée d'un seul `<img>` depuis un fragment HTML (drop navigateur).
- **`_add_entries_to_mosaic()`** — point de sortie commun : ajoute les entrées téléchargées à `state.images_data` et rafraîchit la mosaïque.
- **`_suppress_empty_hint()`** / **`_restore_empty_hint()`** — masquent/restaurent le message d'accueil du canvas pendant l'import (voir section dédiée).

## Les trois points d'entrée

### 1. Saisie manuelle d'URL — `WebImportDialog`

Menu Fichier > "Importer depuis le web" (`menubar_qt.py:90`, aussi menu contextuel canvas `context_menus_qt.py:104`) → `show_web_import_dialog(parent, canvas, callbacks)`.

- Fenêtre non-modale simple : un champ `QLineEdit` + OK/Annuler.
- `_process_url()` (appelé par Entrée ou OK) : normalise l'URL saisie (`https://` ajouté automatiquement si l'utilisateur a tapé juste `example.com`, détecté via présence d'un `.` et absence d'espace), rejette si toujours pas `http(s)://` après normalisation.
- **Ce flux ne fait rien lui-même en matière de réseau** : la fenêtre se ferme (`self.close()`) puis délègue directement à `_resolve_and_download()`, exactement comme le drop de lien/fichier raccourci (flux 2 et 3) — les trois points d'entrée convergent vers la même résolution asynchrone, nécessaire pour bénéficier de `extract_images_from_js_loops()` sur ce chemin aussi.

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

Point de convergence unique des trois flux :

1. **Court-circuit rapide** : `_url_looks_like_image(url)` vérifie l'extension de l'URL (liste `_IMAGE_URL_EXTS`, inclut `.svg` contrairement à `IMAGE_EXTS` du reste du projet — SVG n'est pas rendu par MosaicView mais l'URL est reconnue comme "probablement une image" avant téléchargement). Si oui → téléchargement direct sans passer par le worker de résolution, l'URL de la page est déjà connue comme étant l'image elle-même.
2. Sinon → `_ResolveWorker` (thread) fait un GET complet (pas de vrai HEAD — certains serveurs répondent différemment ou refusent HEAD) avec les en-têtes navigateur complets (`_BROWSER_HEADERS`), lit `Content-Type` :
   - Contient `image` → une seule image (`resolved_image.emit(url, page_title)`).
   - Sinon → union de `extract_images_from_html()` (HTML statique) et `extract_images_from_js_loops()` (boucles JS, voir section dédiée), émise via `resolved_html.emit(image_urls, page_title)`.
3. Erreur réseau : `HTTPError` avec code 403 → traité spécifiquement (`kind="forbidden"`, message dédié `web.web_drop_forbidden_*` — un site avec protection anti-bot type Cloudflare rejette souvent une requête sans les bons en-têtes) ; toute autre erreur → message générique `web.web_drop_error_*`.
4. **Garde-vie du worker** : référence stockée dans `canvas._resolve_workers` (liste, attribut dynamique sur le canvas) jusqu'à la fin du thread — même famille de piège anti-GC documentée pour d'autres workers du projet (voir skill `comicvine-metadata-fetch`, section workers Qt, et `archive-image-loading`, section `_orphan_workers`).
5. **Overlay + masquage du message d'accueil** : `_resolve_and_download()` affiche un overlay rouge `web.web_analyzing_page` ("Analyse de la page en cours...") dès le lancement, et appelle `_suppress_empty_hint(canvas)` — voir section dédiée plus bas. Sans cet overlay immédiat, l'attente du GET réseau laisserait le canvas visuellement vide, perçu comme un freeze par l'utilisateur.

## Extraction des images générées par JavaScript — `extract_images_from_js_loops()`

Nécessaire car le site officiel de MosaicView construit ses vignettes de démonstration de sa page d'accueil (`index.html`, mosaïque animée + aperçus des deux panneaux) entièrement en JavaScript, sans jamais écrire de balise `<img src="...">` statique dans le HTML servi — `extract_images_from_html()` seule n'y trouve que 3 images (les icônes) sur 36.

**Approche retenue : analyse purement textuelle du `<script>`, sans jamais exécuter le JavaScript.** Une première tentative avec un rendu réel via `QWebEnginePage` (Chromium headless) a été essayée et abandonnée — elle capturait un instantané figé du DOM à un instant arbitraire, ne voyait jamais toutes les images d'une animation en boucle qui ne les affiche jamais toutes simultanément, et un simple délai d'attente plus long n'y changeait rien (le compte plafonnait, quel que soit le temps d'observation). Le vrai constat : les chemins d'images sont des **littéraux déjà présents dans le texte du script** — inutile d'exécuter quoi que ce soit pour les connaître, il suffit de les lire.

Deux formes de boucle reconnues (`_JS_FOR_LOOP_RE` et `_JS_RECURSIVE_LOOP_RE`) :
- **Vrai `for`** : `for (let i = A; i <[=] B; i++) { ... elt.src = EXPR; ... }`.
- **Boucle simulée par récursion + `setTimeout`** (animations de remplissage échelonné) : `let VAR = START; function NAME() { if (VAR >= END) { ...; return; } ...elt.src = EXPR...; VAR++; ...setTimeout(NAME, délai); }` — pattern rencontré sur le site officiel (`fillOne()`), où le vrai `for` ne crée que les balises `<img>` vides, remplies plus tard par cette fonction récursive.

Pour chaque boucle trouvée, `process_body()` (fonction interne) :
1. Repère toute expression `.src = EXPR;` dans le corps de la boucle (`_find_matching_brace()` — comptage d'accolades pour délimiter le corps, suffisant pour du JS non minifié).
2. Vérifie que `EXPR` est **intégralement** couverte par `_JS_CONCAT_TERM_RE` (littéraux `'...'`/`"..."`, identifiants nus, ou appels à un argument type `pad(i)`) — si un segment de l'expression n'est pas reconnu (ex. `pad(order[i])`, un accès de tableau dont le contenu n'existe qu'à l'exécution), l'expression entière est **rejetée**, jamais traitée partiellement : mieux vaut ne pas générer d'URL que d'en générer une tronquée/fausse.
3. Pour chaque valeur de la variable de boucle sur son intervalle, résout chaque terme via `_resolve_js_number()` (littéral, constante `const`/`let`/`var` numérique déjà capturée par `_JS_CONST_NUM_RE`, constante string via `_JS_CONST_STR_RE`, ou la variable de boucle elle-même avec un éventuel `+N`/`-N`) et les fonctions de padding simples (`function pad(n) { return String(n).padStart(W, 'C'); }`, capturées par `_JS_PAD_FN_RE`) — reconstruit littéralement le chemin, résolu en URL absolue via `urljoin`.

**Piège corrigé pendant le développement** : la variable de boucle (`i`) doit primer sur toute "constante" numérique globale de même nom — `for (let i = 0; ...)` matche accidentellement aussi le pattern générique de déclaration `let NAME = NUM;` utilisé pour capturer les constantes, ce qui polluait la résolution si l'ordre de priorité n'était pas explicite dans `_resolve_js_number()`.

**Limite assumée** : ce parseur ne peut reconstruire que ce que le JavaScript référence explicitement dans son texte. Sur le site officiel, le script ne référence que `GRID_SIZE=24` images numérotées + 9 "Fantastic 09" par panneau (soit 33 après dédoublonnage des chevauchements + 3 icônes = 36 trouvées) — le dossier réel `Thumbnails/` du serveur contient en fait 72 fichiers (36+36), mais les 36 non référencés par le JS ne sont simplement jamais visibles depuis la page, donc hors du périmètre de ce que "importer depuis cette page" peut raisonnablement signifier.

## Message d'accueil du canvas pendant l'import — `_suppress_empty_hint()` / `_restore_empty_hint()`

Sans ce mécanisme, l'overlay rouge de progression (`show_canvas_text`, voir skill `canvas-overlay-progress`) se centre verticalement au même endroit que le message d'accueil "Déposez ici..." de `mosaic_canvas.py` (`_show_empty_message`/`_center_empty_items`) quand le canvas est vide (aucun comic ouvert) — les deux textes se chevauchent visuellement.

Réutilise le pattern déjà établi ailleurs dans le projet (`panel_widget.py`/`scan_dialog_qt.py`) : l'attribut dynamique `canvas._loading` (positionné à `True`, il empêche `render_mosaic()` de recréer le message d'accueil) :

- **`_suppress_empty_hint(canvas)`** : `canvas._loading = True` + supprime immédiatement les items déjà affichés dans la scène (`canvas._empty_items`) — nécessaire car `_loading=True` empêche seulement la *recréation* future du message, pas la suppression de celui déjà présent. Appelé au tout début de `_resolve_and_download()` et dans `WebDownloadController.__init__()` (couvre aussi le cas image directe, qui saute la phase de résolution).
- **`_restore_empty_hint(canvas, callbacks)`** : `canvas._loading = False` + appelle `render_mosaic()` pour réafficher immédiatement le message si le canvas est resté vide. Appelé sur tout chemin d'échec (aucune image trouvée, erreur réseau/403, téléchargement totalement infructueux).
- Le chemin de succès (`_add_entries_to_mosaic()`) remet aussi `canvas._loading = False` directement (nécessaire — sinon `_loading` resterait bloqué à `True` indéfiniment après un import réussi, empêchant le message de réapparaître si l'utilisateur vide le canvas plus tard).

## En-têtes HTTP — pourquoi deux jeux différents

`_BROWSER_HEADERS` (résolution d'URL/page) vs `_IMAGE_HEADERS` (téléchargement d'image déjà identifiée) :
- `_BROWSER_HEADERS` annonce le support AVIF/WebP dans `Accept` — nécessaire pour qu'un serveur retourne du HTML normal plutôt qu'une erreur anti-bot.
- `_IMAGE_HEADERS` **n'annonce pas** AVIF/WebP — certains CDN à négociation de contenu (mentionné : Optimole) reconvertissent l'image servie vers ces formats modernes si le client dit les supporter, alors qu'on veut souvent le format d'origine (ex. JPEG) pour rester cohérent avec ce que l'utilisateur voyait sur la page. Un `User-Agent` seul ne suffit pas à passer certaines protections — d'où l'en-tête complet imitant un vrai navigateur dans les deux cas.

## Téléchargement effectif — `_DownloadWorker`

- Pour chaque URL d'image : **encodage du path** via `urllib.parse.quote` avant la requête (`quote(parsed_img_url.path)`, `safe='/'` implicite) — corrige un bug où une URL contenant un espace ou un accent non encodé (ex. `Fantastic 09 01.jpg`) faisait lever `InvalidURL` par `urllib.request` et échouait systématiquement, alors qu'un vrai navigateur encode ces caractères automatiquement.
- **Retry sur échec réseau transitoire** (`_DOWNLOAD_MAX_RETRIES=2` tentatives supplémentaires, `_DOWNLOAD_RETRY_DELAY_S=1.0` seconde entre chaque) — nécessaire face à un `503 Service Unavailable` ponctuel sur une image isolée d'un gros lot (charge côté serveur). Pas de retry sur un échec de validation PIL (image invalide) — retenter le même contenu corrompu ne changerait rien. Le flag d'annulation est revérifié après la boucle de retry pour ne pas continuer si l'utilisateur a cliqué "Annuler" pendant l'attente.
- Téléchargement (`urllib.request`, timeout 10s), validation via PIL (`Image.open` + `img.verify()`) — une URL qui prétendait être une image mais ne l'est pas est silencieusement ignorée (`except: pass`), pas d'erreur bloquante par image individuelle.
- **Correction d'extension** : si le format réel détecté par PIL (`img.format`) diffère de l'extension déduite du nom de fichier dans l'URL, le nom est corrigé (ex. une image servie en `.jpg` mais réellement WebP devient `image.webp`) — même logique de cohérence que la détection de type d'archive (voir skill `archive-image-loading`, `detect_archive_type`), mais ici au niveau image individuelle plutôt qu'archive entière.
- **Nom de fichier** : dérivé du chemin de l'URL (`os.path.basename`) si présent et avec extension, sinon généré (`{page_title}_{idx+1:03d}.jpg`) — `page_title` vient du domaine de la page source (`urlparse(url).netloc`, `www.` retiré).
- **Préfixe `NEW-`** : ajouté si un comic est déjà ouvert (`state.current_file is not None`) OU si `state.images_data` contient déjà des entrées OU si d'autres entrées ont déjà été ajoutées **dans ce même lot** de téléchargement (`new_entries` non vide) — même convention que les autres chemins d'ajout après-coup (voir skill `archive-image-loading`, section chargement d'images isolées).
- `entry["source_archive"] = "web"` — marqueur d'origine, distinct de `"loose"` (fichier local isolé) utilisé ailleurs.
- Chaque entrée passe par `create_entry()` (`entries.py`) comme tout autre chemin de chargement — voir skill `archive-image-loading` pour ce que fait cette fonction (décodage PIL, hash MD5, dimensions, GIF animé...).

## Interaction avec la mosaïque et les panneaux — `_add_entries_to_mosaic()`

Point de sortie unique de `WebDownloadController._on_finished` (les trois flux convergent maintenant tous vers le téléchargement asynchrone, voir plus haut) :

1. Si `state.images_data` est vide (aucun comic ouvert), crée un point undo (`save_state()`) **avant** d'ajouter — cohérent avec le pattern des autres chemins d'ajout après-coup (voir skill `archive-image-loading`, `_ImageLoadWorker`/`_start_image_load`).
2. `state.images_data.extend(entries)` puis retri par tri naturel (`_natural_sort_key`, importé depuis `archive_loader.py` — **pas dupliqué**, un seul point de tri naturel dans tout le projet).
3. `state.modified = True`, `state.needs_renumbering = True` si au moins une image est présente (voir skill `renumbering` pour ce que ce flag active/désactive — pas de déclenchement automatique de renumérotation ici, juste l'activation du bouton).
4. `canvas._loading = False` (voir section message d'accueil ci-dessus), `clear_selection()`, `render_mosaic()`, rafraîchissement des boutons/toolbar via les callbacks fournis.

**Dict `callbacks`** (contrat entre ce module et le panneau appelant, voir `PanelWidget._web_import_callbacks()`, `panel_widget.py:1486`) : `state`, `save_state`, `render_mosaic`, `update_button_text`, `update_create_cbz_button`, `clear_selection` — même style de contrat que `_get_batch_callbacks()` documenté dans le skill `batch-processing`, mais plus petit et spécifique à ce module. Toujours passer par cette méthode plutôt que de construire le dict à la main dans un nouveau call-site.

**Chaque panneau (panel1/panel2 en split-view) a son propre `_web_import_callback` et ses propres callbacks** — un import web déclenché sur le panneau 2 n'écrit jamais dans `images_data` du panneau 1 (voir skill `panels`).

## Comment étendre

- **Ajouter une nouvelle source de drop reconnue** (ex. un format de raccourci supplémentaire) : suivre le pattern `.url`/`.webloc` dans `PanelWidget._handle_dropped_paths()` — parser le fichier pour en extraire une URL `http(s)`, puis appeler `_resolve_and_download()` avec cette URL, exactement comme les deux cas existants.
- **Changer la détection "URL qui ressemble à une image"** : uniquement `_url_looks_like_image()`/`_IMAGE_URL_EXTS` — attention, cette liste diffère volontairement de `IMAGE_EXTS` (inclut `.svg`), ne pas les fusionner sans vérifier l'impact.
- **Changer le comportement anti-bot** (en-têtes, gestion 403) : `_BROWSER_HEADERS` dans `_ResolveWorker.run()` — un site qui bloque encore malgré ces en-têtes n'a pas de contournement supplémentaire prévu (pas de rotation de User-Agent, pas de proxy).
- **Étendre `extract_images_from_js_loops()` à un nouveau pattern JS** : ajouter une nouvelle regex de détection de boucle (sur le modèle de `_JS_FOR_LOOP_RE`/`_JS_RECURSIVE_LOOP_RE`) puis appeler `process_body()` avec `(loop_var, start, end, body)` — la logique de résolution des expressions (`_JS_CONCAT_TERM_RE`, `_resolve_js_number()`) est déjà partagée et n'a normalement pas besoin d'être dupliquée. Rester strict sur le rejet des expressions partiellement reconnues (ne jamais générer une URL tronquée).
- **Ajuster les paramètres de retry réseau** : `_DOWNLOAD_MAX_RETRIES`/`_DOWNLOAD_RETRY_DELAY_S` en tête de la section `_DownloadWorker`.
- Respecter les 8 règles UI Qt obligatoires du CLAUDE.md pour `WebImportDialog` (non-modale déjà en place, `_wt()` pour le titre déjà en place).

## Pièges connus

- **`canvas._resolve_workers` est un attribut dynamique**, pas déclaré dans `__init__` de `MosaicCanvas` — créé à la volée par `getattr(canvas, '_resolve_workers', [])` au premier appel. Ne pas supposer qu'il existe avant tout appel à `_resolve_and_download`.
- **Le drop de lien depuis un navigateur peut porter plusieurs représentations MIME simultanément** (`hasUrls()` ET `hasHtml()`) — toujours préférer le fragment HTML pour cibler l'image exacte quand disponible, ne pas se contenter de `web_urls[0]` par simplicité si `mime.hasHtml()` est vrai.
- **Un `.url`/`.webloc` sans URL valide à l'intérieur retombe silencieusement dans `regular_paths`** — pas un message d'erreur dédié, le fichier suit le chemin normal de chargement et échouera probablement avec un message générique "format non supporté" (voir skill `archive-image-loading`).
- **Le worker de téléchargement (`_DownloadWorker`) n'émet aucune erreur par image individuelle** — une image qui échoue après épuisement des retries (404, format invalide, panne serveur prolongée) est juste absente du résultat final ; seul `no_images` (aucune image téléchargée sur tout le lot) déclenche un message utilisateur.
- **`extract_images_from_js_loops()` ne peut pas deviner ce que le JavaScript ne référence pas explicitement** — un dossier serveur peut contenir plus de fichiers que ce que le script charge réellement (voir section dédiée, cas du site officiel : 72 fichiers sur disque, 36 référencés et récupérables). Ce n'est pas un bug à corriger, c'est une limite structurelle de l'analyse statique face à du contenu qui n'existe que côté serveur.
- **Ne jamais tenter de rendu JS réel (`QWebEnginePage`/Chromium headless) pour ce module** — approche déjà essayée et abandonnée (voir section `extract_images_from_js_loops`) : un instantané du DOM, même différé ou ré-échantillonné dans le temps, ne peut pas capturer une animation qui ne montre jamais toutes ses images simultanément, et ça introduit une dépendance/latence lourde pour un problème qui se résout entièrement par lecture statique du texte du script.
