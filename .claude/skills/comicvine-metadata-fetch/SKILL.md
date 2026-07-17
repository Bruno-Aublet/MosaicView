---
name: comicvine-metadata-fetch
description: Localiser ou modifier la récupération de métadonnées en ligne depuis l'API ComicVine (recherche de série, choix d'issue, URL directe, clé API). Utiliser dès qu'une tâche touche à comicvine_scraper.py, comicvine_dialog_qt.py, ou write_comic_metadata_from_scraper.
---

# Récupération de métadonnées ComicVine — MosaicView

Télécharge des métadonnées depuis l'API web ComicVine (comicvine.gamespot.com) et les écrit dans le `ComicInfo.xml` de l'archive ouverte. Inspiré de [comic-vine-scraper](https://github.com/cbanack/comic-vine-scraper) par Cory Banack (Apache 2.0), crédité dans l'UI. Ne pas confondre avec l'**édition manuelle** du même fichier XML (formulaire local, aucun réseau) — voir skill `comicinfo-metadata-editor`, qui référence celui-ci pour tout ce qui est en ligne.

## Fichiers clés

- **`modules/qt/comicvine_scraper.py`** — cœur réseau pur (aucun Qt) : appels HTTP à l'API, parsing des réponses JSON, gestion des erreurs. C'est le seul fichier qui parle vraiment à ComicVine.
- **`modules/qt/comicvine_dialog_qt.py`** — fenêtre wizard 2 pages (recherche série → choix issue), le point d'entrée principal "Récupérer les métadonnées".
- **`modules/qt/comicvine_url_dialog_qt.py`** — fenêtre intercalée qui permet de coller une URL ComicVine connue (issue ou série) au lieu de chercher par nom ; contient aussi `_parse_comicvine_url()`, réutilisée ailleurs dans le projet (voir plus bas).
- **`modules/qt/comicvine_apikey_dialog_qt.py`** — fenêtre de saisie/effacement de la clé API utilisateur, stockée via `ConfigManager.get_/set_comicvine_api_key()`.
- **`modules/qt/comicvine_update_check_qt.py`** — retélécharge les métadonnées d'un issue déjà scrapé et propose une mise à jour si des différences sont détectées. Sujet voisin par le nom mais indépendant de `update_checker_qt.py` (voir skill `update-checker`), qui vérifie les mises à jour de MosaicView **lui-même**, pas des métadonnées d'un comic.
- **`modules/qt/comic_info.py`** — `write_comic_metadata_from_scraper()` et `diff_comic_metadata()` : c'est **ce fichier**, pas celui-ci, qui écrit réellement dans le `ComicInfo.xml` (voir skill `comicinfo-metadata-editor` pour son fonctionnement interne — sérialisation XML, champ `Notes` de traçabilité, `sync_pages_in_xml_data`).
- **`modules/qt/batch_metadata_dialog_qt.py`** — assistant d'import ComicVine **en masse** sur tout un dossier (voir skill `batch-processing`) : réutilise `comicvine_scraper.py` et `_ComicVineDialog` directement, avec caches de recherche/issues partagés entre fichiers du lot (`shared_search_cache`/`shared_issues_cache`).
- **`modules/qt/library_db.py`** — la Bibliothèque (voir skill `library`) réutilise `_parse_comicvine_url` (via le champ virtuel de recherche `comicvine_format`) pour retrouver, parmi les comics indexés, ceux dont le `ComicInfo.xml` porte déjà une URL ComicVine dans l'ancien ou le nouveau format de domaine — sans jamais faire d'appel réseau elle-même.

## Vue d'ensemble du flux

1. L'utilisateur déclenche "Récupérer les métadonnées" (menu Fichier, menu contextuel canvas, ou vignette) → `PanelWidget._fetch_metadata()` (`panel_widget.py:2476`).
2. Si aucune clé API n'est enregistrée, `show_apikey_dialog()` s'ouvre d'abord ; à validation, rappelle `_fetch_metadata()` via `dlg.accepted.connect(...)`.
3. `show_comicvine_url_dialog()` s'ouvre en premier (URL directe optionnelle) :
   - Champ pré-rempli avec l'URL ComicVine déjà présente dans les métadonnées locales (`comic_metadata['web']`), si le fichier a déjà été scrapé.
   - Bouton "Télécharger" → parse l'URL (`_parse_comicvine_url`), reconnaît `issue` ou `series`, télécharge en arrière-plan (`_UrlFetchWorker`).
     - Issue trouvée → écrit directement (`write_comic_metadata_from_scraper`) et ferme.
     - Série trouvée → enchaîne sur le wizard de recherche, page 2 (`show_comicvine_dialog(..., preselected_series=series)`).
   - Bouton "Rechercher" → ouvre directement le wizard de recherche par nom (`show_comicvine_dialog`), sans passer par une URL.
4. Wizard `_ComicVineDialog` (`comicvine_dialog_qt.py`) — 2 pages dans un `QStackedWidget` :
   - **Page 1 (`_Page1Series`)** — recherche par nom (pré-rempli avec `comic_metadata['series']` ou déduit du nom de fichier via `_clean_filename_for_search`), tableau de résultats, aperçu de la couverture du fichier local **et** de la couverture du 1er épisode de la série survolée (deux blocs encadrés côte à côte). Sélection auto de la meilleure correspondance (`difflib.SequenceMatcher` + proximité d'année si une année est détectée dans le terme de recherche).
   - **Page 2 (`_Page2Issues`)** — liste paginée de tous les issues de la série choisie (chargement multi-pages automatique via `_IssuesWorker`, 100 par page), présélection de l'issue dont le numéro correspond (`_guess_issue_number()`, déduit de `comic_metadata['number']` ou du texte de recherche saisi). À la confirmation, télécharge les détails complets (`_MetadataWorker` → `get_issue_details`) puis `write_comic_metadata_from_scraper`.
5. En mode batch (`batch=True`), les boutons Ignorer/Annuler ont un comportement différent (passer au fichier suivant du lot / arrêter tout le lot après confirmation) — voir skill `batch-processing`.

## API ComicVine — fonctions publiques de `comicvine_scraper.py`

| Fonction | Usage |
|---|---|
| `search_series(api_key, search_terms, page=1)` | Recherche de séries (volumes) par nom — page 1 du wizard |
| `get_series_issues(api_key, series_id, page=1)` | Liste des issues d'une série — page 2 du wizard, paginée |
| `get_series_summary(api_key, series_id)` | Résumé minimal d'une série (utilisé par le flux URL directe → série) |
| `get_series_details(api_key, series_id)` | Éditeur/année/genres d'une série, appelé en interne par `get_issue_details` |
| `get_issue_details(api_key, issue_id)` | Détails complets d'un issue → dict directement mappé sur les champs `comic_metadata` |

`get_issue_details` fait le plus gros du travail de mapping :
- Champs simples (titre, série, numéro, résumé nettoyé du HTML via `_strip_html`, URL de la page ComicVine dans `web`...).
- Date de couverture (`cover_date` "YYYY-MM-DD") éclatée en `year`/`month`/`day`.
- Crédits créatifs (`person_credits`) répartis par rôle via `role_map` (writer/penciller/inker/colorist/letterer/cover_artist/editor) — un rôle ComicVine non reconnu dans `role_map` est silencieusement ignoré.
- Personnages/équipes/lieux/story arcs joints en chaîne via `_join_names()`.
- `_cover_image_url` (clé préfixée `_`, pas un champ ComicInfo) — URL de couverture pour affichage, jamais écrite dans le XML.

**Toutes ces fonctions peuvent lever une exception** (réseau ou API) — jamais de retour `None` silencieux sur erreur, contrairement à un fichier local introuvable ailleurs dans le projet.

## Gestion des erreurs — `ComicVineNetworkError` et clés de traduction

Particularité de ce module : les erreurs réseau/API portent une **clé de traduction**, pas un message déjà résolu, pour permettre une retraduction dynamique si l'utilisateur change de langue pendant qu'un message d'erreur est affiché (règle CLAUDE.md n°2).

- `ComicVineNetworkError(translation_key)` — sous-classe de `RuntimeError`, résout immédiatement le message pour l'exception Python elle-même mais garde `translation_key` accessible.
- `_classify_api_error(status_code)` — mappe les codes `status_code` documentés par ComicVine (100-105, 107) vers une clé (`_API_ERROR_KEYS`). Code non catalogué → `RuntimeError` générique avec le code et le texte brut (nettoyé via `_neutralize_html_chars`).
- `_classify_network_error(exc)` — reconnaît `requests.exceptions.Timeout`/`ConnectionError` et les mappe vers une clé de traduction, plutôt que d'afficher la stack technique brute (hôte, port, `NameResolutionError`...).
- **Transit à travers un signal Qt** (`Signal(str)`, qui ne porte qu'une chaîne figée) : `error_to_signal_payload(exc)` encode la clé avec un préfixe interne (`\x00i18n:`) ; côté UI, `error_message_fn(payload)` décode et retourne un `lambda: _(key)` réutilisable directement comme `status_fn` d'`ErrorDialog`/label de statut. **Pattern à reproduire pour tout nouveau worker Qt de ce module** — ne jamais faire transiter un message déjà résolu si l'erreur peut survenre pendant qu'un changement de langue est possible.
- **Retry automatique** (`_get_json`) : jusqu'à 3 tentatives, délais `[5, 10]` secondes, uniquement pour les exceptions réseau (pas pour un code d'erreur API métier, qui n'a pas de raison de réussir au 2ᵉ essai).
- **Rate limiting** (`_wait_rate_limit`) : délai minimum de 2 secondes entre deux requêtes, global au module (`_next_query_time`), pas par worker — évite de dépasser le quota ComicVine en cas de requêtes rapprochées (ex. wizard qui enchaîne recherche série → couverture 1er issue → détails issue en quelques secondes).

## Sécurité

- **Clé API jamais loggée en clair** : `_redact_api_key(message)` masque `api_key=...` dans tout message d'erreur réseau avant affichage (les exceptions `requests` incluent souvent l'URL complète appelée) — voir règle CLAUDE.md sécurité n°2, ce fichier en est la référence.
- **Seules les URLs `http://`/`https://` sont suivies** : `_parse_image_url()` (couvertures) filtre explicitement le schéma avant de retourner une URL de téléchargement — une réponse API anormale ne doit pas pouvoir faire télécharger un `file://`/`ftp://`. Même filtre redondant côté UI dans `_ImageWorker.run()` (`comicvine_dialog_qt.py`) en défense en profondeur.
- **Neutralisation HTML** (`_neutralize_html_chars`) sur les messages d'erreur bruts renvoyés par l'API — évite qu'un `QLabel` bascule en rich-text sur un message contenant `<`/`>`.

## Workers Qt et cycle de vie (`comicvine_dialog_qt.py`)

Chaque appel réseau tourne dans un `QThread` dédié (`_SearchWorker`, `_IssuesWorker`, `_FirstIssueWorker`, `_ImageWorker`, `_MetadataWorker`) — jamais d'appel synchrone au scraper depuis le thread UI.

- **`_park_running_worker(worker)`** (`_ComicVineDialog`) : pattern de préservation d'un `QThread` encore actif quand un nouveau worker vient remplacer la référence Python (ex. l'utilisateur change de série avant que le chargement des issues précédentes soit fini). Détache les slots (`finished`/`error`) puis garde une référence dans `_dying_workers` (liste de classe) jusqu'à ce que `isRunning()` redevienne `False`. **Ne teste pas via le signal `finished`** : `_ImageWorker`/`_MetadataWorker` redéfinissent `finished` en `Signal(bytes)`/`Signal(dict)` custom qui masque le `QThread.finished` natif et s'émet avant la fin réelle de `run()` — seul `isRunning()` est fiable ici. Même pattern que le hook `_dying_workers` documenté dans `project_qthread_lifecycle.md` (mémoire projet) pour d'autres crashes QThread du projet.
- **`_UpdateDiffDialog`** (`comicvine_update_check_qt.py`) utilise une liste globale de module `_running_workers` (pas une liste de classe) pour le même problème — deux mécanismes distincts pour le même besoin selon le fichier, ne pas supposer qu'ils partagent un état.
- **Caches partagés en mode batch** : `shared_search_cache`/`shared_issues_cache` (dicts passés depuis `batch_metadata_dialog_qt.py`) évitent de re-télécharger la même recherche/liste d'issues pour plusieurs fichiers consécutifs d'un même lot appartenant à la même série. Clé de cache normalisée par `_cache_key_for_terms()` (retire préfixes numériques de nommage, dates, numéro d'issue isolé) — **pas** la chaîne brute tapée par l'utilisateur.

## Vérification de mise à jour (`comicvine_update_check_qt.py`)

Distinct du wizard de recherche : reprend l'**URL déjà enregistrée** dans le `ComicInfo.xml` local (`comic_metadata['web']`) pour retélécharger et comparer, sans repasser par une recherche.

- Point d'entrée : `show_comicvine_update_check(parent, state, api_key, issue_id, on_done, busy_widget)`. `issue_id` vient de `get_source_comicvine_issue_id()` (`comic_info.py`), qui extrait l'ID numérique d'une URL ComicVine **d'issue** stockée dans `Web` (ignore les URLs de série — `4050-xxxxx`, non exploitables ici).
- `diff_comic_metadata(local_meta, remote_meta)` (`comic_info.py`) compare champ par champ sur `_DIFF_FIELDS` (sous-ensemble de `_SCRAPER_FIELD_MAP` réellement renseigné par `get_issue_details` — exclut `imprint`, jamais renseigné par le scraper). Ne reporte **jamais** un champ vide côté distant comme différence (pas de suppression silencieuse d'une donnée locale).
- Aucune différence → `InfoDialog` "déjà à jour". Différences → `_UpdateDiffDialog` liste les champs (`metadata.{field_key}` + statut `added`/`modified`), bouton "tout mettre à jour" réécrit tout via `write_comic_metadata_from_scraper` (pas de mise à jour champ par champ sélective).
- Point d'entrée visible dans l'UI : bouton "Vérifier les mises à jour" dans l'éditeur ComicInfo (`comicinfo_dialog_qt.py`, voir skill `comicinfo-metadata-editor`), visible seulement si une URL d'issue est détectée. Pattern `busy_widget` : le bouton se désactive et affiche un texte "Vérification..." pendant la requête, restauré après — état retraduisible via la property Qt `is_checking_updates` (lue dans le `_retranslate()` du parent si l'utilisateur change de langue pendant l'attente).

## Reconnaissance d'URL ComicVine — `_parse_comicvine_url()`

Dans `comicvine_url_dialog_qt.py`, réutilisée par `comic_info.py::get_source_comicvine_issue_id` et par `library_db.py` (champ virtuel `comicvine_format`, voir skill `library`). Reconnaît **deux générations de domaine** :
- Format actuel : `comicvine.gamespot.com/.../4000-XXXXX` (issue) ou `4050-XXXXX` (série).
- Ancien domaine `comicvine.com` (avant unification) : préfixe `37-` ou `4000-` pour une issue, `4050-` pour une série.

Retourne `(kind, id)` avec `kind ∈ {"issue", "series"}`, ou `None` si aucun format reconnu. **Point unique à mettre à jour si ComicVine change encore de format d'URL** — trois consommateurs différents en dépendent (ce dialogue, `comic_info.py`, `library_db.py`), ne pas dupliquer la regex ailleurs.

## Où sont écrites les métadonnées récupérées

Ce skill ne couvre que la **récupération** (réseau). L'écriture effective dans `entry["bytes"]` du `ComicInfo.xml` — sérialisation XML, préservation de `<Pages>`, champ `Notes` de traçabilité (`"MosaicView: metadata retrieved on {date}."`), regénération de `state.comic_metadata` — est entièrement documentée dans le skill **`comicinfo-metadata-editor`**, fonction `write_comic_metadata_from_scraper()`. Toujours lire ce skill avant de toucher au format d'écriture.

## Points d'entrée UI

| Entrée | Callback | Condition d'activation |
|---|---|---|
| Menu Fichier > Métadonnées > Récupérer | `menubar_qt.py:354` → `callbacks['fetch_metadata']` | `has_file` |
| Menu contextuel canvas > Métadonnées > Récupérer | `context_menus_qt.py:304` | fichier ouvert |
| Menu Fichier > Métadonnées > Créer/Éditer ComicInfo | `menubar_qt.py:359-365` → `callbacks['edit_comicinfo']` | voir skill `comicinfo-metadata-editor` |
| Menu > Changer la clé API | `menubar_qt.py:367` → `callbacks['change_apikey']` | toujours actif |
| Bouton "Vérifier les mises à jour" dans l'éditeur ComicInfo | `comicinfo_dialog_qt.py::_on_check_updates_clicked` | URL d'issue détectée dans `comic_metadata['web']` |
| Import ComicVine en masse | `batch_metadata_dialog_qt.py` | menu Fichier > Batch, voir skill `batch-processing` |

Tous convergent vers `PanelWidget._fetch_metadata()` / `_edit_comicinfo()` / `_change_apikey()` / `_check_comicvine_updates()` (`panel_widget.py:2413-2510`) — un seul endroit par action à modifier quel que soit le point d'entrée UI.

## Comment étendre

- **Ajouter un nouveau champ récupéré depuis ComicVine** : l'ajouter au dict retourné par `get_issue_details()` (`comicvine_scraper.py`), puis à `_SCRAPER_FIELD_MAP` (`comic_info.py`) pour qu'il soit effectivement écrit dans le XML par `write_comic_metadata_from_scraper`. S'il doit aussi être comparable par la vérification de mise à jour, vérifier qu'il n'est pas exclu de `_DIFF_FIELDS`.
- **Changer le mapping de rôles créatifs** : uniquement `role_map` dans `get_issue_details()` — un rôle ComicVine non listé est actuellement ignoré silencieusement, pas une erreur à corriger sans le signaler.
- **Ajouter un nouveau format d'URL ComicVine reconnu** : uniquement `_parse_comicvine_url()` (`comicvine_url_dialog_qt.py`) — vérifier les 3 consommateurs listés plus haut après modification.
- Respecter les 8 règles UI Qt obligatoires du CLAUDE.md pour toute modification des 4 fenêtres de ce module (non-modales déjà en place, `_wt()` pour les titres déjà en place, `OverlayTooltip` si un tooltip est ajouté — aucun actuellement dans ces fichiers).

## Pièges connus

- **Ne jamais afficher un message d'erreur réseau sans passer par `error_to_signal_payload`/`error_message_fn`** — un message déjà résolu affiché directement resterait figé dans l'ancienne langue si l'utilisateur change de langue pendant que l'erreur est visible (règle CLAUDE.md n°2).
- **Ne jamais appeler le scraper depuis le thread UI** — toujours un `QThread` dédié, même pour un appel a priori rapide (l'API peut mettre plusieurs secondes à répondre, voire jusqu'à 3 × 15s en cas de retry réseau).
- **`_park_running_worker` doit être appelé avant d'écraser la référence à un worker existant** — sinon crash `QThread: Destroyed while thread is still running` si l'utilisateur enchaîne des actions rapidement (changement de série, fermeture de la fenêtre pendant un chargement).
- **`get_source_comicvine_issue_id` ignore les URLs de série** — un `ComicInfo.xml` dont `Web` pointe vers une série (pas un issue précis) ne peut pas déclencher de vérification de mise à jour ; c'est voulu, pas un bug à corriger silencieusement.
