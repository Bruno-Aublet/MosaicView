---
name: web-import
description: Localiser ou modifier l'import d'images depuis le web (saisie manuelle d'URL, drop d'un lien depuis un navigateur, drop d'un fichier .url/.webloc). Utiliser dès qu'une tâche touche à web_import_qt.py, WebImportDialog, ou _resolve_and_download.
---

# Import web — MosaicView

Télécharge une ou plusieurs images depuis Internet (une image directe, ou toutes les images d'une page HTML — statiques ou générées par JavaScript) et les ajoute à la mosaïque. Trois façons d'y arriver, toutes convergent vers les mêmes fonctions de résolution/téléchargement.

Autre source d'image externe avec une architecture proche (worker `QThread`, overlay de progression, ajout à la mosaïque) : voir skill `scan` (numérisation depuis un scanner physique via WIA), qui réutilise directement `_web_import_callbacks()` de `panel_widget.py`.

## Fichier unique — `modules/qt/web_import_qt.py`

Tout vit dans ce seul fichier (~1000 lignes) :
- **`WebImportDialog`** — fenêtre de saisie manuelle d'une URL (menu Fichier).
- **`_resolve_and_download()`** — résolution asynchrone d'une URL (image directe ou page HTML), point d'entrée commun aux trois flux (saisie manuelle incluse — voir plus bas).
- **`_ResolveWorker`** (`QThread`) — télécharge la page/l'image pour déterminer son type (GET + `Content-Type`), extrait les URLs d'images si c'est une page HTML, sans bloquer l'UI.
- **`_DownloadWorker`** (`QThread`) — télécharge effectivement chaque image trouvée, avec retry sur échec réseau transitoire.
- **`WebDownloadController`** — orchestre `_DownloadWorker` + l'overlay de progression sur le canvas (texte rouge + bouton Annuler, même pattern visuel que le chargement d'archive).
- **`extract_images_from_html()`** — parsing HTML statique pour extraire les `<img src="...">` déjà présents dans le HTML brut.
- **`extract_images_from_js_loops()`** — extraction complémentaire des images générées par JavaScript via des boucles (voir section dédiée plus bas).
- **`_extract_single_img_src()`** — extraction ciblée d'un seul `<img>` depuis un fragment HTML (drop navigateur).
- **`_WebDropQueue`** / **`_drop_queue(canvas)`** — file des imports d'un panneau, qui garantit que les images arrivent dans la mosaïque dans l'ordre des drops (voir section dédiée).
- **`_add_entries_to_mosaic()`** / **`_append_entries()`** — point de sortie commun : ajoute les entrées téléchargées en fin de `state.images_data` (après une éventuelle opération en cours) et rafraîchit la mosaïque.
- **`_suppress_empty_hint()`** / **`_restore_empty_hint()`** — masquent/restaurent le message d'accueil du canvas pendant l'import (voir section dédiée).

## Les trois points d'entrée

### 1. Saisie manuelle d'URL — `WebImportDialog`

Menu Fichier > "Importer depuis le web" (`_populate_file_menu` dans `menubar_qt.py`, aussi menu contextuel canvas `show_canvas_context_menu` dans `context_menus_qt.py`) → `show_web_import_dialog(parent, canvas, callbacks)`.

- Fenêtre non-modale simple : un champ `QLineEdit` + OK/Annuler.
- `_process_url()` (appelé par Entrée ou OK) : normalise l'URL saisie (`https://` ajouté automatiquement si l'utilisateur a tapé juste `example.com`, détecté via présence d'un `.` et absence d'espace), rejette si toujours pas `http(s)://` après normalisation.
- **Ce flux ne fait rien lui-même en matière de réseau** : la fenêtre se ferme (`self.close()`) puis délègue directement à `_resolve_and_download()`, exactement comme le drop de lien/fichier raccourci (flux 2 et 3) — les trois points d'entrée convergent vers la même résolution asynchrone, nécessaire pour bénéficier de `extract_images_from_js_loops()` sur ce chemin aussi.

### 2. Drop d'un lien depuis un navigateur — `mosaic_canvas.py::dropEvent`

Un lien glissé depuis la barre d'adresse ou une image glissée depuis une page web arrive comme `QMimeData` avec `hasUrls()` mais **sans** `toLocalFile()` (pas un fichier local) — voir skill `drag-and-drop`, flux 4 "drop entrant de fichiers/dossiers/URLs externes", pour le contexte plus large de ce `dropEvent`.

- La branche `elif mime.hasUrls() and event.source() is not self` de `MosaicCanvas.dropEvent` sépare `local_paths` (fichiers) de `web_urls` (chaînes `http(s)://`).
- Si `web_urls` et pas de `local_paths` : tente d'abord d'extraire l'URL **précise de l'image droppée** via `mime.hasHtml()` — un drop d'image depuis un navigateur porte souvent un fragment `text/html` contenant le `<img src="...">` exact de l'élément survolé, plus fiable que l'URL de la page entière (`web_urls[0]`) qui peut être juste l'URL de la page si le navigateur ne fournit que ça. `_extract_single_img_src(html_fragment, base_url)` (parsing via `lxml` si disponible, sinon `html.parser` stdlib en fallback) résout les URLs relatives via `urljoin`.
- Trouvé → `self._web_import_callback([image_url])` (une seule image ciblée). Sinon → `self._web_import_callback(web_urls)` (les URLs brutes du mime, potentiellement des pages à résoudre).
- `_web_import_callback` est câblé dans `PanelWidget.__init__` (`self._canvas._web_import_callback = self._handle_dropped_web_urls`) vers `PanelWidget._handle_dropped_web_urls()`, qui appelle `_resolve_and_download()` pour **chaque** URL de la liste — donc un drop multi-URL (rare mais possible) lance une résolution asynchrone par URL, en parallèle ; chacune réserve sa place dans la file du panneau (`_WebDropQueue`), dans l'ordre de la liste.

### 3. Drop d'un fichier raccourci `.url`/`.webloc`

Distinct du drop de lien : ici c'est un **fichier local** (`toLocalFile()` non vide) glissé depuis l'Explorateur Windows ou le Finder macOS, mais qui contient lui-même une URL à l'intérieur.

- Géré dans `PanelWidget._handle_dropped_paths()` (`panel_widget.py`), **avant** que le fichier ne soit passé au routeur normal de chargement (`handle_dropped_paths`/`_load_files`, voir skill `archive-image-loading`) — ce sont les deux seules extensions interceptées à ce niveau en plus de `.mvdb` (voir skill `library`).
- **`.url`** (raccourci Windows) : parsé via `configparser` (format INI, section `[InternetShortcut]`, clé `URL`). Si absent ou pas `http(s)`, tombe silencieusement dans `regular_paths` (traité comme un fichier normal, qui échouera probablement au chargement).
- **`.webloc`** (raccourci macOS) : parsé via `plistlib` (format XML plist, clé `URL`).
- Dans les deux cas, l'URL extraite est passée à **la même fonction** `_resolve_and_download()` que le drop de lien — aucune différence de traitement une fois l'URL extraite du fichier.

## Résolution asynchrone — `_resolve_and_download()` / `_ResolveWorker`

Point de convergence unique des trois flux :

0. **Réservation de la place dans la file** : avant tout accès réseau, `_drop_queue(canvas).reserve(state)` attribue à l'import son numéro d'ordre (voir section `_WebDropQueue`), transmis ensuite à `download_and_add_web_images(..., drop_seq)`. Chaque chemin de sortie qui ne lance pas de téléchargement libère cette place par `queue.complete(drop_seq)` : page sans image (`_on_resolved_html`), erreur réseau/403 (`_on_error`), fermeture du fichier pendant l'analyse (`_cancel_on_close` local).
1. **Court-circuit rapide** : `_url_looks_like_image(url)` vérifie l'extension de l'URL (liste `_IMAGE_URL_EXTS`, inclut `.svg` contrairement à `IMAGE_EXTS` du reste du projet — SVG n'est pas rendu par MosaicView mais l'URL est reconnue comme "probablement une image" avant téléchargement). Si oui → téléchargement direct sans passer par le worker de résolution, l'URL de la page est déjà connue comme étant l'image elle-même.
2. Sinon → `_ResolveWorker` (thread) fait un GET (pas de vrai HEAD — certains serveurs répondent différemment ou refusent HEAD) avec les en-têtes navigateur complets (`_BROWSER_HEADERS`), lit `Content-Type` :
   - Contient `image` → une seule image (`resolved_image.emit(url, page_title)`). Le corps n'est **pas lu** ici : `_DownloadWorker` télécharge l'image ensuite (la lire ici la ferait télécharger deux fois).
   - Sinon → page lue par `_read_limited(response, _MAX_PAGE_BYTES)` (voir « Limites »), puis union de `extract_images_from_html()` (HTML statique) et `extract_images_from_js_loops()` (boucles JS, voir section dédiée), tronquée à `_MAX_EXTRACTED_URLS`, émise via `resolved_html.emit(image_urls, page_title)`.
3. Erreur réseau : `HTTPError` avec code 403 → traité spécifiquement (`kind="forbidden"`, message dédié `web.web_drop_forbidden_*` — un site avec protection anti-bot type Cloudflare rejette souvent une requête sans les bons en-têtes) ; toute autre erreur → message générique `web.web_drop_error_*`.
4. **Garde-vie du worker** : référence stockée dans `canvas._resolve_workers` (liste, attribut dynamique sur le canvas) jusqu'à la fin du thread — même famille de piège anti-GC documentée pour d'autres workers du projet (voir skill `comicvine-metadata-fetch`, section workers Qt, et `archive-image-loading`, section `_orphan_workers`).
5. **Overlay + masquage du message d'accueil** : `_resolve_and_download()` affiche un overlay rouge `web.web_analyzing_page` ("Analyse de la page en cours...") dès le lancement, et appelle `_suppress_empty_hint(canvas)` — voir section dédiée plus bas. Sans cet overlay immédiat, l'attente du GET réseau laisserait le canvas visuellement vide, perçu comme un freeze par l'utilisateur.

## Extraction des images générées par JavaScript — `extract_images_from_js_loops()`

Nécessaire car le site officiel de MosaicView construit ses vignettes de démonstration de sa page d'accueil (`index.html`, mosaïque animée + aperçus des deux panneaux) entièrement en JavaScript, sans jamais écrire de balise `<img src="...">` statique dans le HTML servi — `extract_images_from_html()` seule n'y trouve que 3 images (les icônes) sur 36.

**Approche : analyse purement textuelle du `<script>`, sans jamais exécuter le JavaScript.** Un rendu réel (`QWebEnginePage`, Chromium headless) ne convient pas : il capture un instantané figé du DOM à un instant arbitraire, et ne voit jamais toutes les images d'une animation en boucle qui ne les affiche jamais toutes simultanément, quel que soit le délai d'observation. Les chemins d'images sont des **littéraux déjà présents dans le texte du script** — inutile d'exécuter quoi que ce soit pour les connaître, il suffit de les lire.

Deux formes de boucle reconnues (`_JS_FOR_LOOP_RE` et `_JS_RECURSIVE_LOOP_RE`) :
- **Vrai `for`** : `for (let i = A; i <[=] B; i++) { ... elt.src = EXPR; ... }`.
- **Boucle simulée par récursion + `setTimeout`** (animations de remplissage échelonné) : `let VAR = START; function NAME() { if (VAR >= END) { ...; return; } ...elt.src = EXPR...; VAR++; ...setTimeout(NAME, délai); }` — pattern rencontré sur le site officiel (`fillOne()`), où le vrai `for` ne crée que les balises `<img>` vides, remplies plus tard par cette fonction récursive.

Pour chaque boucle trouvée, `process_body()` (fonction interne) :
1. Repère toute expression `.src = EXPR;` dans le corps de la boucle (`_find_matching_brace()` — comptage d'accolades pour délimiter le corps, suffisant pour du JS non minifié).
2. Vérifie que `EXPR` est **intégralement** couverte par `_JS_CONCAT_TERM_RE` (littéraux `'...'`/`"..."`, identifiants nus, ou appels à un argument type `pad(i)`) — si un segment de l'expression n'est pas reconnu (ex. `pad(order[i])`, un accès de tableau dont le contenu n'existe qu'à l'exécution), l'expression entière est **rejetée**, jamais traitée partiellement : mieux vaut ne pas générer d'URL que d'en générer une tronquée/fausse.
3. Pour chaque valeur de la variable de boucle sur son intervalle, résout chaque terme via `_resolve_js_number()` (littéral, constante `const`/`let`/`var` numérique déjà capturée par `_JS_CONST_NUM_RE`, constante string via `_JS_CONST_STR_RE`, ou la variable de boucle elle-même avec un éventuel `+N`/`-N`) et les fonctions de padding simples (`function pad(n) { return String(n).padStart(W, 'C'); }`, capturées par `_JS_PAD_FN_RE`) — reconstruit littéralement le chemin, résolu en URL absolue via `urljoin`.

**Limites (page hostile ou démesurée)** : une boucle de plus de `_MAX_JS_LOOP_SPAN` tours (5 000) est ignorée (`for (i = 0; i < 1e12; i++)` produirait sinon des milliards d'adresses) ; au plus `_MAX_JS_LOOPS` boucles (200) examinées par page ; une expression `.src = …` de plus de `_MAX_JS_EXPR_LENGTH` caractères (1 000) est ignorée **avant** la regex — `_JS_CONCAT_TERM_RE` a un coût quadratique sur une longue suite de lettres sans terminaison (8 000 caractères : 2,4 s ; 1 Mo : des heures) ; l'extraction s'arrête à `_MAX_EXTRACTED_URLS` adresses (2 000, même limite pour `extract_images_from_html`). Voir aussi « Limites des téléchargements ». Testé par `tests/test_web_import_limits.py`.

**Piège** : la variable de boucle (`i`) doit primer sur toute "constante" numérique globale de même nom dans `_resolve_js_number()` — `for (let i = 0; ...)` matche accidentellement aussi le pattern générique de déclaration `let NAME = NUM;` utilisé pour capturer les constantes, donc l'ordre de priorité entre les deux doit rester explicite, jamais implicite.

**Limite assumée** : ce parseur ne peut reconstruire que ce que le JavaScript référence explicitement dans son texte. Sur le site officiel, le script ne référence que `GRID_SIZE=24` images numérotées + 9 "Fantastic 09" par panneau (soit 33 après dédoublonnage des chevauchements + 3 icônes = 36 trouvées) — le dossier réel `Thumbnails/` du serveur contient en fait 72 fichiers (36+36), mais les 36 non référencés par le JS ne sont simplement jamais visibles depuis la page, donc hors du périmètre de ce que "importer depuis cette page" peut raisonnablement signifier.

## Message d'accueil du canvas pendant l'import — `_suppress_empty_hint()` / `_restore_empty_hint()`

Sans ce mécanisme, l'overlay rouge de progression (`show_canvas_text`, voir skill `canvas-overlay-progress`) se centre verticalement au même endroit que le message d'accueil "Déposez ici..." de `mosaic_canvas.py` (`_show_empty_message`/`_center_empty_items`) quand le canvas est vide (aucun comic ouvert) — les deux textes se chevauchent visuellement.

Même mécanisme que le chargement d'images (`panel_widget.py`) et la numérisation (`scan_dialog_qt.py`) : un jeton tenu sur le canvas (`MosaicCanvas.hold_empty_hint`/`release_empty_hint`, skill `canvas-overlay-progress`) empêche `render_mosaic()` de recréer le message d'accueil tant qu'il n'est pas libéré. Le jeton d'un import est `_web_hint(drop_seq)` = `("web", drop_seq)`, propre à chaque import. **Piège** : avec un simple booléen partagé, le premier import terminé ferait réapparaître le message sous un second encore en cours.

- **`_suppress_empty_hint(canvas, drop_seq)`** : tient le jeton + supprime immédiatement les items déjà affichés dans la scène (`canvas._empty_items`) — nécessaire car le jeton empêche seulement la *recréation* future du message, pas la suppression de celui déjà présent. Appelé au tout début de `_resolve_and_download()` et dans `WebDownloadController.__init__()` (couvre aussi le cas image directe, qui saute la phase de résolution).
- **`_restore_empty_hint(canvas, callbacks, drop_seq)`** : libère le jeton + appelle `render_mosaic()`, qui réaffiche le message si le canvas est resté vide et qu'aucun autre chargement n'est en cours. Appelé sur tout chemin d'échec (aucune image trouvée, erreur réseau/403, téléchargement totalement infructueux).
- Le jeton est aussi libéré à la fermeture du fichier (`_cancel_on_close`, des deux phases), quand le document a changé (`_on_finished`, `_WebDropQueue.complete`) et sur le chemin de succès (`_add_entries_to_mosaic(canvas, entries, callbacks, drop_seq)`, avant même un ajout mis en attente) — un jeton jamais libéré empêcherait le message de réapparaître si l'utilisateur vide le canvas plus tard.

## En-têtes HTTP — pourquoi deux jeux différents

`_BROWSER_HEADERS` (résolution d'URL/page) vs `_IMAGE_HEADERS` (téléchargement d'image déjà identifiée) :
- `_BROWSER_HEADERS` annonce le support AVIF/WebP dans `Accept` — nécessaire pour qu'un serveur retourne du HTML normal plutôt qu'une erreur anti-bot.
- `_IMAGE_HEADERS` **n'annonce pas** AVIF/WebP — certains CDN à négociation de contenu (mentionné : Optimole) reconvertissent l'image servie vers ces formats modernes si le client dit les supporter, alors qu'on veut souvent le format d'origine (ex. JPEG) pour rester cohérent avec ce que l'utilisateur voyait sur la page. Un `User-Agent` seul ne suffit pas à passer certaines protections — d'où l'en-tête complet imitant un vrai navigateur dans les deux cas.

## Téléchargement effectif — `_DownloadWorker`

- **State du panneau transmis explicitement** : `WebDownloadController` passe `callbacks['state']` au constructeur de `_DownloadWorker`, qui le lit dans `run()` (préfixe `NEW-` ci-dessous). **Ne jamais lire le singleton `_state_module.state` dans `run()`** : il pointe sur le panneau actif au moment de l'exécution, peut-être l'autre en split-view. `tests/test_check_no_deferred_singleton.py` scanne ce fichier et échoue sur toute lecture non protégée du singleton (seules formes admises : `callbacks.get('state') or _state_module.state`, ou dans un `if state is None:`).
- Pour chaque URL d'image : **encodage du path** via `urllib.parse.quote` avant la requête (`quote(parsed_img_url.path)`, `safe='/'` implicite) — sans cet encodage, une URL contenant un espace ou un accent (ex. `Fantastic 09 01.jpg`) fait lever `InvalidURL` par `urllib.request`, alors qu'un navigateur encode ces caractères automatiquement.
- **Retry sur échec réseau transitoire** (`_DOWNLOAD_MAX_RETRIES=2` tentatives supplémentaires, `_DOWNLOAD_RETRY_DELAY_S=1.0` seconde entre chaque) — nécessaire face à un `503 Service Unavailable` ponctuel sur une image isolée d'un gros lot (charge côté serveur). Pas de retry sur un échec de validation PIL (image invalide) — retenter le même contenu corrompu ne changerait rien. Le flag d'annulation est revérifié après la boucle de retry pour ne pas continuer si l'utilisateur a cliqué "Annuler" pendant l'attente.
- Téléchargement (`urllib.request`, timeout 10 s par lecture) lu par `_read_limited(response, _MAX_IMAGE_BYTES, self._cancel_flag)` (voir « Limites des téléchargements » ; une image trop grande n'est pas retentée), validation via PIL (`Image.open` + `img.verify()`). Une image manquée n'arrête jamais le lot, mais n'est jamais avalée non plus : `run()` la range dans `failures`, liste de `(kind, nom, détail)` — `"not_downloaded"` (erreur réseau après les retries, détail = message brut de l'exception), `"too_large"` (`DownloadTooLargeError`) ou `"not_image"` (reçu, mais pas une image valide) — émise avec les entrées par `finished(new_entries, failures)`. Le nom est celui que donne `_url_display_name(url)` : fin du chemin décodée, sinon nom du site, jamais l'adresse entière (elle peut porter `nom:motdepasse@`), caractères de contrôle remplacés. `WebDownloadController._on_finished`, sur le thread de l'interface, ouvre la fenêtre « Fichiers ignorés » (`archive_loader._MessageDialog`, texte brut, texte recalculé à chaque changement de langue par `_download_failure_line`, 20 lignes au plus puis « … ») quand des images ont été ajoutées **et** d'autres manquées ; rien si aucune n'a été obtenue (message de `_on_no_images`), si l'utilisateur a annulé, ou si le document a changé. Clés `messages.warnings.files_ignored.web_not_downloaded` / `web_too_large` / `web_not_image`, traduites à l'affichage, jamais dans le worker. **Piège** : avalées, ces erreurs donnaient une BD incomplète sans que l'utilisateur le sache (seul le cas « aucune image » était signalé). Testé par `tests/test_web_import_limits.py` (`test_missed_image_name_never_contains_credentials`).
- **Correction d'extension** : si le format réel détecté par PIL (`img.format`) diffère de l'extension déduite du nom de fichier dans l'URL, le nom est corrigé (ex. une image servie en `.jpg` mais réellement WebP devient `image.webp`) — même logique de cohérence que la détection de type d'archive (voir skill `archive-image-loading`, `detect_archive_type`), mais ici au niveau image individuelle plutôt qu'archive entière. Table `ext_map` : `JPEG` et `MPO` (photo de téléphone, un JPEG avec une image annexe) donnent `jpg`, les autres formats leur nom en minuscules — sans l'entrée `MPO`, une photo deviendrait `.mpo`, extension qu'aucune liste d'images du projet ne reconnaît. Les pages lues dans une archive suivent la même règle, par `archive_names.name_for_real_format` (skill `archive-image-loading`).
- **Nom de fichier** : dérivé du chemin de l'URL (`os.path.basename`) si présent et avec extension, sinon généré (`{page_title}_{idx+1:03d}.jpg`) — `page_title` vient du site de la page source, calculé par `page_title_from_url(url)` (haut de `web_import_qt.py`, utilisé par `_ResolveWorker.run` et `_resolve_and_download`) : nom d'hôte seul (`urlparse(url).hostname`, en minuscules, `www.` retiré). **Piège** : `netloc` contient aussi l'identifiant, le mot de passe et le port d'une adresse `https://nom:motdepasse@site:8080/…`, qui se retrouveraient en clair dans le nom de chaque page puis dans le CBZ. Testé par `tests/test_web_import_limits.py` (`test_page_title_never_contains_credentials_or_port`).
- **Préfixe `NEW-`** : ajouté si un comic est déjà ouvert (`state.current_file is not None`) OU si `state.images_data` contient déjà des entrées OU si d'autres entrées ont déjà été ajoutées **dans ce même lot** de téléchargement (`new_entries` non vide) — même convention que les autres chemins d'ajout après-coup (voir skill `archive-image-loading`, section chargement d'images isolées).
- `entry["source_archive"] = "web"` — marqueur d'origine, distinct de `"loose"` (fichier local isolé) utilisé ailleurs.
- Chaque entrée passe par `create_entry()` (`entries.py`) comme tout autre chemin de chargement — voir skill `archive-image-loading` pour ce que fait cette fonction (décodage PIL, hash MD5, dimensions, GIF animé...).

## Limites des téléchargements — `_read_limited(response, max_bytes, cancel_flag=None)`

Toute lecture d'une réponse réseau de ce module passe par cette fonction, jamais par `response.read()` : le `timeout` de `urlopen` vaut pour **chaque** lecture, pas pour le téléchargement entier — une réponse lente et sans fin, ou énorme, remplirait la mémoire sans jamais s'arrêter. Lecture par blocs de 64 Ko ; lève `DownloadTooLargeError` si `Content-Length` ou le volume reçu dépasse `max_bytes`, `TimeoutError` après `_DOWNLOAD_DEADLINE_S` (120 s) ; rend `None` si `cancel_flag[0]` passe à vrai. Limites : `_MAX_PAGE_BYTES` (20 Mo, page HTML analysée), `_MAX_IMAGE_BYTES` (200 Mo, image). Constantes regroupées avant `extract_images_from_js_loops`. Testé par `tests/test_web_import_limits.py`.

## Interaction avec la mosaïque et les panneaux — `_add_entries_to_mosaic()`

Appelé uniquement par `_WebDropQueue.complete()`, quand le lot est le prochain dans l'ordre des drops (voir section suivante). `WebDownloadController._on_finished` ne l'appelle jamais directement : il remet son lot à la file (`queue.complete(drop_seq, canvas, new_entries, callbacks)`), même vide.

0. `_on_finished` libère sa place sans rien ajouter si le document a changé pendant le téléchargement : `WebDownloadController.__init__` capture `state.doc_generation` (state du panneau, `callbacks['state']`) et `_on_finished` compare (voir skill `file-close`). Sans ça, les images arriveraient dans le panneau vide ou dans le comic ouvert entre-temps. La file refait ce contrôle au moment d'ajouter, avec la génération capturée au drop : un lot terminé en avance a pu attendre pendant que le fichier était fermé.
   - **Arrêt à la fermeture du fichier** (skill `file-close`, « Arrêt des opérations à la fermeture ») : `WebDownloadController` inscrit `_cancel_on_close` par `register_cancel_on_close` (retiré dans `_on_finished`) : il pose le drapeau d'annulation (arrêt à l'image suivante, une requête en cours ne pouvant être interrompue), masque l'overlay, débranche les signaux du worker et le confie à `park_qthread`. **Piège** : le contrôleur n'est retenu que par le bouton Annuler de l'overlay (lambda de `_show_cancel_item`) ; masquer l'overlay le rend libérable alors que son thread tourne encore — sans `park_qthread`, le `_DownloadWorker` pourrait être détruit en cours d'exécution.
   - **Phase d'analyse de la page** (`_resolve_and_download`) : un `_cancel_on_close` local masque l'overlay « analyse en cours » et pose `closed[0]` ; les trois slots du `_ResolveWorker` (`_on_resolved_image`, `_on_resolved_html`, `_on_error`) se retirent de la liste puis ne font rien si `closed[0]` est posé — sinon le téléchargement démarrerait après la fermeture, sur le document suivant.
1. `_add_entries_to_mosaic` libère le jeton du message d'accueil de cet import (voir section message d'accueil ci-dessus) puis confie l'ajout à `state.run_when_idle` : pendant une opération en arrière-plan sur le document (rotation…), les images sont ajoutées à sa fin, sinon son annulation les effacerait (skill `undo-redo`). L'ajout lui-même est `_append_entries(state, entries, callbacks)` :
2. `save_state()` **avant** d'ajouter (pousse le snapshot initial si la mosaïque est vide, sinon normalement sans effet).
3. `state.images_data.extend(entries)`, **sans retri** : les images vont en fin de mosaïque. Les noms issus d'URL sont arbitraires (identifiants de CDN, hash...) et ne disent rien de l'ordre voulu ; un tri par nom (`_natural_sort_key`) mélangeait les images déposées et réordonnait aussi les pages du comic déjà ouvert. Ne pas le réintroduire. L'ordre des drops est garanti par `_WebDropQueue`, l'ordre des images d'une même page par la boucle séquentielle de `_DownloadWorker`.
4. `state.modified = True`, `state.needs_renumbering = True` si au moins une image est présente (voir skill `renumbering` pour ce que ce flag active/désactive — pas de déclenchement automatique de renumérotation ici, juste l'activation du bouton).
5. `save_state()` **après** l'ajout — point d'annulation propre à l'import (testé par `tests/test_additions_undo_point.py`). **Piège** : sans lui, Annuler retirait les images en défaisant aussi l'action précédente, et Refaire ne les rendait jamais.
6. `clear_selection()`, `render_mosaic()`, rafraîchissement des boutons/toolbar via les callbacks fournis.

Un lot qui arrive pendant le chargement d'une archive ou d'un PDF est gardé à la suite des pages chargées (`archive_loader._install_loaded_pages`, skill `archive-image-loading`).

**Dict `callbacks`** (contrat entre ce module et le panneau appelant, voir `PanelWidget._web_import_callbacks()`, `panel_widget.py`) : `state`, `save_state`, `render_mosaic`, `update_button_text`, `update_create_cbz_button`, `clear_selection` — même style de contrat que `_get_batch_callbacks()` documenté dans le skill `batch-processing`, mais plus petit et spécifique à ce module. Toujours passer par cette méthode plutôt que de construire le dict à la main dans un nouveau call-site.

**Chaque panneau (panel1/panel2 en split-view) a son propre `_web_import_callback` et ses propres callbacks** — un import web déclenché sur le panneau 2 n'écrit jamais dans `images_data` du panneau 1 (voir skill `panels`).

## Ordre des drops — `_WebDropQueue`

Les imports tournent en parallèle (un `_ResolveWorker` puis un `_DownloadWorker` par drop) et peuvent finir dans le désordre : une petite image déposée après une grosse finit souvent avant. La file garantit que les lots arrivent dans la mosaïque dans l'ordre où l'utilisateur les a déposés.

- **Une file par panneau** : attribut dynamique `canvas._web_drop_queue`, créé à la demande par `_drop_queue(canvas)` (même style que `canvas._resolve_workers`). En split-view, chaque panneau a la sienne.
- **`reserve(state)`** : appelé au drop (début de `_resolve_and_download`, ou `download_and_add_web_images` sans `drop_seq`), avant tout accès réseau. Renvoie un numéro croissant et retient `state.doc_generation`.
- **`complete(seq, canvas=None, entries=None, callbacks=None)`** : marque le lot terminé. Tant que le prochain numéro attendu est terminé, la file le sort : rien si `entries` est vide, rien si `doc_generation` a changé depuis le drop (et `canvas._loading = False`), sinon `_add_entries_to_mosaic`. Un lot terminé en avance attend donc les lots réservés avant lui. Sans effet sur un numéro déjà libéré (idempotent).
- **Ajout en fin de mosaïque uniquement** : une réorganisation manuelle faite pendant les téléchargements n'est jamais dérangée. C'est pour ça que l'ordre est garanti par la file plutôt que par un numéro posé sur les entrées puis une insertion au milieu de `images_data`.
- **Où la place est libérée** (chaque réservation doit l'être exactement une fois, sinon les lots suivants attendent indéfiniment) :
  - `_resolve_and_download` : page sans image (`_on_resolved_html`), erreur réseau/403 (`_on_error`), fichier fermé pendant l'analyse (`_cancel_on_close` local) ;
  - `download_and_add_web_images` appelée avec une liste vide et un `drop_seq` ;
  - `WebDownloadController._on_finished` : lot téléchargé, vide (annulation, aucune image valide) ou visant un document changé ;
  - `WebDownloadController._cancel_on_close` : fichier fermé pendant le téléchargement (ses signaux étant débranchés, `_on_finished` ne sera plus appelé).
- **Prix assumé** : un lot rapide déposé après un lot lent n'apparaît qu'une fois le lent terminé.

## Comment étendre

- **Ajouter une nouvelle source de drop reconnue** (ex. un format de raccourci supplémentaire) : suivre le pattern `.url`/`.webloc` dans `PanelWidget._handle_dropped_paths()` — parser le fichier pour en extraire une URL `http(s)`, puis appeler `_resolve_and_download()` avec cette URL, exactement comme les deux cas existants.
- **Changer la détection "URL qui ressemble à une image"** : uniquement `_url_looks_like_image()`/`_IMAGE_URL_EXTS` — attention, cette liste diffère volontairement de `IMAGE_EXTS` (inclut `.svg`), ne pas les fusionner sans vérifier l'impact.
- **Changer le comportement anti-bot** (en-têtes, gestion 403) : `_BROWSER_HEADERS` dans `_ResolveWorker.run()` — un site qui bloque encore malgré ces en-têtes n'a pas de contournement supplémentaire prévu (pas de rotation de User-Agent, pas de proxy).
- **Étendre `extract_images_from_js_loops()` à un nouveau pattern JS** : ajouter une nouvelle regex de détection de boucle (sur le modèle de `_JS_FOR_LOOP_RE`/`_JS_RECURSIVE_LOOP_RE`) puis appeler `process_body()` avec `(loop_var, start, end, body)` — la logique de résolution des expressions (`_JS_CONCAT_TERM_RE`, `_resolve_js_number()`) est déjà partagée et n'a normalement pas besoin d'être dupliquée. Rester strict sur le rejet des expressions partiellement reconnues (ne jamais générer une URL tronquée).
- **Ajuster les paramètres de retry réseau** : `_DOWNLOAD_MAX_RETRIES`/`_DOWNLOAD_RETRY_DELAY_S` en tête de la section `_DownloadWorker`.
- Respecter les 8 règles UI Qt obligatoires du CLAUDE.md pour `WebImportDialog` (non-modale déjà en place, `_wt()` pour le titre déjà en place).

## Pièges connus

- **Toute place réservée dans `_WebDropQueue` doit être libérée** par `complete()`, sur tous les chemins de sortie, y compris échec, annulation et fermeture du fichier. Un nouveau chemin de sortie dans `_resolve_and_download` ou `WebDownloadController` qui l'oublie bloque silencieusement tous les imports suivants du panneau : leurs téléchargements se terminent, leur overlay disparaît, mais les images n'arrivent jamais.
- **Ne jamais retrier `images_data` par nom après un import web**, ni insérer les images ailleurs qu'en fin de mosaïque (voir `_add_entries_to_mosaic`).

- **`canvas._resolve_workers` est un attribut dynamique**, pas déclaré dans `__init__` de `MosaicCanvas` — créé à la volée par `getattr(canvas, '_resolve_workers', [])` au premier appel. Ne pas supposer qu'il existe avant tout appel à `_resolve_and_download`.
- **Le drop de lien depuis un navigateur peut porter plusieurs représentations MIME simultanément** (`hasUrls()` ET `hasHtml()`) — toujours préférer le fragment HTML pour cibler l'image exacte quand disponible, ne pas se contenter de `web_urls[0]` par simplicité si `mime.hasHtml()` est vrai.
- **Un `.url`/`.webloc` sans URL valide à l'intérieur retombe silencieusement dans `regular_paths`** — pas un message d'erreur dédié, le fichier suit le chemin normal de chargement et échouera probablement avec un message générique "format non supporté" (voir skill `archive-image-loading`).
- **Une image manquée ne bloque pas le lot** — une image qui échoue après épuisement des retries (404, format invalide, panne serveur prolongée) est absente du résultat, puis listée dans la fenêtre « Fichiers ignorés » en fin de téléchargement (voir « Téléchargement effectif ») ; `no_images` (aucune image téléchargée sur tout le lot) garde son propre message.
- **`extract_images_from_js_loops()` ne peut pas deviner ce que le JavaScript ne référence pas explicitement** — un dossier serveur peut contenir plus de fichiers que ce que le script charge réellement (voir section dédiée, cas du site officiel : 72 fichiers sur disque, 36 référencés et récupérables). Ce n'est pas un bug à corriger, c'est une limite structurelle de l'analyse statique face à du contenu qui n'existe que côté serveur.
- **Ne jamais tenter de rendu JS réel (`QWebEnginePage`/Chromium headless) pour ce module** (voir section `extract_images_from_js_loops`) : un instantané du DOM, même différé ou ré-échantillonné dans le temps, ne peut pas capturer une animation qui ne montre jamais toutes ses images simultanément, et ça introduit une dépendance/latence lourde pour un problème qui se résout entièrement par lecture statique du texte du script.
