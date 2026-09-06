---
name: batch-metadata-import
description: Localiser ou modifier l'import de métadonnées ComicVine en lot (ouverture séquentielle de la fenêtre ComicVine pour chaque fichier d'un dossier). Utiliser dès qu'une tâche touche à batch_metadata_dialog_qt.py, _BatchMetadataOrchestrator, ou au traitement "Import des métadonnées en lot".
---

# Import de métadonnées en lot — MosaicView

Un des 8 traitements par lot du projet (skill `batch-processing`, **à lire en premier** pour l'architecture commune : contrat `batch_callbacks`, deux points d'entrée menu/drop). **Seul flux batch interactif** — pas de traitement silencieux en arrière-plan comme les 6 autres (aucun `_ThreadSignals`/`_ProgressDialog`/`threading.Thread` classique), mais l'ouverture **séquentielle** de la fenêtre ComicVine interactive (skill `comicvine-metadata-fetch`) pour chaque fichier trouvé, où l'utilisateur choisit lui-même la série/l'issue à chaque étape. Dans `modules/qt/batch_metadata_dialog_qt.py` (1219 lignes).

## `_MetadataConfirmDialog` — deux checkboxes, pas une

Contrairement à `_ConfirmDialog` (réutilisé par CBR/CB7/CBT/PDF/IMG) et `_RecompressConfirmDialog` (recompression), ce flux a sa **propre** fenêtre de confirmation avec 2 options indépendantes :

- **Checkbox suppression permanente** — visible **seulement si des fichiers non-CBZ sont présents dans le lot** (`self._has_non_cbz = any(not f.lower().endswith('.cbz') for f in files)`) ; accompagnée d'un avertissement rouge italique (`dialogs.batch_metadata.cbz_warning`, `rgb(220,0,0)`) — le lot d'import de métadonnées peut inclure CBR/CB7/CBT/PDF en plus des CBZ (voir section conversion à la volée), et ceux-ci doivent être convertis en CBZ pour recevoir un `ComicInfo.xml`, ce qui implique de supprimer/remplacer l'original.
- **Checkbox "ignorer les fichiers ayant déjà un ComicInfo.xml"** (`skip_existing`) — toujours visible, indépendante du type de fichier.

## `_BatchMetadataOrchestrator` — séquenceur, pas un thread de traitement

**Pas de boucle `for` synchrone** comme les 6 autres flux — la classe pilote une séquence pas à pas, chaque étape déclenchée par un signal Qt (`_LoadWorker.done`, `on_next`/`on_cancel_batch` de la fenêtre ComicVine, etc.), puisque le traitement de chaque fichier attend une **interaction utilisateur** (choix de série/issue dans la fenêtre ComicVine) avant de continuer.

### Chargement d'un state allégé — `_load_state_for_file` (`batch_metadata_dialog_qt.py:586`)

**Ne charge pas la mosaïque complète du fichier** — construit un `AppState` minimal contenant seulement :
1. La **couverture** (première image par tri naturel) — pour l'aperçu visuel dans la fenêtre ComicVine.
2. Le `ComicInfo.xml` existant s'il y en a un (parsé via `parse_comic_info_xml`, skill `comicinfo-metadata-editor`) — pour pré-remplir le formulaire avec les métadonnées déjà présentes plutôt que de repartir de zéro.
3. Une entrée **manifeste** (`orig_name: "__cbz_manifest__"`, `bytes: None`) qui référence le chemin du CBZ d'origine — pas de contenu, juste un marqueur consulté par `_save_state_for_file` pour savoir comment réécrire le fichier complet à la sauvegarde (voir section suivante).

Pour un fichier **non-CBZ** (`.cbr`/`.cb7`/`.cbt`/`.pdf`), le chemin diverge : extrait la couverture via le loader approprié à ce format puis prépare un **chemin CBZ de destination** — le fichier sera physiquement converti en CBZ au moment de la sauvegarde, pas seulement enrichi de métadonnées. `_LoadWorker` (`QThread`) exécute ce chargement hors du thread UI, avec un mécanisme de séquence anti-race (`self._seq`, incrémenté à chaque nouvelle demande de chargement — un callback `done`/`error` d'un worker obsolète, dont le `seq` ne correspond plus à `self._seq` courant, est ignoré silencieusement plutôt que d'écraser l'état avec une réponse périmée).

### Fenêtre ComicVine réutilisée, pas recréée à chaque fichier

`show_comicvine_dialog(..., batch=True, cbz_filepath=..., on_done=..., batch_index=self._index+1, batch_total=len(self._files), shared_search_cache=self._search_cache, shared_issues_cache=self._issues_cache, on_next=self._on_skip, on_cancel_batch=self.cancel)` — voir skill `comicvine-metadata-fetch` pour le détail de cette fenêtre en mode `batch=True` (compteur "fichier N/total", bouton "Suivant"/"Ignorer" en plus du flux normal). **Caches de recherche partagés entre tous les fichiers du lot** (`_search_cache`/`_issues_cache`, dicts d'instance de l'orchestrateur, transmis tels quels à chaque ouverture) — évite de réinterroger l'API ComicVine pour une série déjà recherchée/résolue plus tôt dans le même lot, optimisation notable si le dossier contient plusieurs tomes de la même série.

Pour le fichier **suivant** (pas le premier), la fenêtre existante n'est pas fermée/recréée — `_update_dialog(state, cbz_filepath)` met à jour son contenu en place (`_on_load_done_next`), et `_rescue_dlg_workers` détache proprement les workers internes de la fenêtre ComicVine du fichier précédent (déconnexion des signaux, `setParent(None)`, référence gardée dans `_ComicVineDialog._dying_workers` jusqu'à leur fin naturelle) avant de la réutiliser pour le fichier suivant — évite de détruire un worker encore en cours (ex. une recherche réseau pas encore terminée pour le fichier précédent) par un changement de contexe prématuré.

Entre le clic sur "Suivant"/"Ignorer" et la fin du chargement du fichier suivant, `_on_next` appelle d'abord `_reset_dialog()` (`batch_metadata_dialog_qt.py:464`) pour vider immédiatement la fenêtre — couverture du fichier local, nom, terme de recherche, tableau de résultats des deux pages, **et la couverture du 1er épisode de la série en page 1** (`_page1.clear_first_issue_cover()`) — avant même que `_update_dialog` ne reçoive l'état du nouveau fichier. Sans cet appel explicite à `clear_first_issue_cover()`, cette couverture-là (bloc du bas de la page 1, distinct de `_page1.set_cover()` qui affiche la couverture du fichier local) reste visuellement celle de la série du fichier précédent jusqu'à ce qu'une nouvelle recherche série aboutisse (`populate_series()`, qui l'efface aussi, mais seulement à ce moment-là) — toute nouvelle donnée effacée par `_update_dialog`/`populate_series` en cours de route doit être vidée aussi dans `_reset_dialog`, sans quoi elle reste affichée entre deux fichiers du lot.

### Sauvegarde — `_save_state_for_file` (`batch_metadata_dialog_qt.py:762`)

Ne s'exécute que si `state.modified` est vrai (l'utilisateur a bien validé des métadonnées pour ce fichier, pas juste consulté). Réécrit le CBZ **complet** dans un fichier temporaire (`.~tmp`) en se basant sur le "manifeste" détecté (`__cbz_manifest__`/`__cbr_manifest__`/`__cbt_manifest__`/`__cb7_manifest__`/`__pdf_manifest__` — un seul type de manifeste par fichier traité) :
- **Manifeste CBZ** : recopie **toutes** les entrées de l'archive originale sauf celles déjà réécrites explicitement (couverture, `ComicInfo.xml` — potentiellement modifiées par l'utilisateur), plus les entrées à `bytes` non vides du state (le XML de métadonnées mis à jour).
- **Manifeste non-CBZ** (CBR/CB7/CBT/PDF) : convertit l'archive source complète vers CBZ à cette occasion (progression rapportée via `progress_cb`, affichée dans l'overlay "Conversion en cours... {pct}%" sur la fenêtre ComicVine elle-même — voir `_show_converting_overlay`/`_on_save_progress`) — l'import de métadonnées **déclenche implicitement** une conversion de format complète pour ces fichiers, pas seulement l'ajout d'un `ComicInfo.xml`.

Réglage de compression ZIP utilisateur appliqué (`zip_compression_kwargs`, skill `zip-compression`), comme les autres flux batch. Suppression de l'original géré séparément selon `permanent_delete` et le type de fichier (non détaillé dans la portion lue, mais cohérent avec le pattern `is_permanent`/`safe_delete_file` des autres flux).

## Annulation — acquis partiel, pas tout-ou-rien

`cancel()` (`batch_metadata_dialog_qt.py:383`) : arrête proprement les workers en cours (chargement et sauvegarde), puis affiche directement le résumé — **les fichiers déjà traités avant l'annulation restent acquis** (leurs métadonnées sont déjà sauvegardées sur disque), seuls le fichier courant et les suivants n'en reçoivent pas. Déclenché soit par le bouton Annuler du batch pendant la fenêtre ComicVine (`on_cancel_batch=self.cancel`), soit potentiellement via `_CancelConfirmDialog` (`batch_metadata_dialog_qt.py:1074`, confirmation avant arrêt — continuer/arrêter, hors détail approfondi dans ce skill).

## Anti-GC — `_active_orchestrators`, mécanisme séparé du reste

**Pas `_register_batch`/`_unregister_batch`** (le registre des 6 autres flux, `batch_dialogs_qt.py`) — ce flux a son propre registre module-level (`_active_orchestrators`, en tête de `batch_metadata_dialog_qt.py`), ajouté dans `_start_batch` et retiré dans le callback `_on_complete` — nécessaire car ce flux n'utilise ni `_ThreadSignals` ni `_ProgressDialog`, la mécanique anti-GC standard ne s'applique pas de la même façon à un orchestrateur piloté par événements plutôt qu'un simple thread de fond.

## Clé API ComicVine — vérifiée avant le lancement, pas pendant

`show_batch_metadata_dialog` (`batch_metadata_dialog_qt.py:1173`) vérifie `get_config_manager().get_comicvine_api_key()` **après** confirmation de `_MetadataConfirmDialog` mais **avant** de créer l'orchestrateur — si absente, ouvre `show_apikey_dialog` (skill `comicvine-metadata-fetch`) d'abord ; l'orchestrateur (`_start_batch`) n'est créé qu'une fois une clé valide obtenue, jamais lancé sans clé API.

## Résumé

`_MetadataSummaryDialog` (`batch_metadata_dialog_qt.py:951`) — statistiques `_done_count`/`_skipped_count`/`_errors` accumulées pendant la séquence, structure propre à ce flux (pas de compteurs de renommage comme les flux d'archive, pas de compteurs par extension comme IMG).

## Comment étendre

- **Ajouter un nouveau format non-CBZ supporté par ce flux** : suivre le pattern manifeste existant (`__xxx_manifest__`) dans `_load_state_for_file`/`_save_state_for_file` — les deux fonctions doivent rester synchronisées (un manifeste créé au chargement doit être reconnu à la sauvegarde).
- **Changer la stratégie de cache partagé** (actuellement deux dicts simples, jamais vidés ni limités en taille pendant tout le lot) : `self._search_cache`/`self._issues_cache` dans `_BatchMetadataOrchestrator.__init__` — voir skill `comicvine-metadata-fetch` pour la structure exacte de ces caches.

## Pièges connus

- **Pas de traitement en arrière-plan silencieux** — contrairement aux 6 autres flux batch, ce flux est fondamentalement interactif ; ne pas chercher une boucle `for` synchrone de traitement, la logique est pilotée par callbacks/signaux Qt.
- **State chargé volontairement incomplet** (couverture + XML seulement, pas la mosaïque) — une tentative d'accéder à `state.images_data` en s'attendant à toutes les pages du fichier échouerait silencieusement (liste courte, 2-3 entrées).
- **Fichier non-CBZ converti implicitement à la sauvegarde** — l'utilisateur qui importe des métadonnées sur un lot mixte CBR/CBZ ne s'attend pas forcément à ce que ses CBR soient convertis en CBZ au passage ; comportement voulu (nécessaire pour écrire un `ComicInfo.xml`), pas un bug, mais à bien comprendre avant de "corriger" un rapport de bug dessus.
- **Registre anti-GC séparé** (`_active_orchestrators`, pas `_register_batch`) — ne pas mélanger les deux mécanismes en copiant du code entre ce flux et les 6 autres.
- **Annulation = acquis partiel** — ne pas supposer un rollback complet du lot à l'annulation, contrairement à un comportement "tout ou rien" qu'on pourrait attendre par défaut.
- **Séquence anti-race (`self._seq`)** — tout ajout d'un nouveau callback asynchrone dans l'orchestrateur doit vérifier `s == self._seq` avant d'agir, sinon un worker périmé pourrait écraser l'état d'un fichier plus récent déjà en cours de traitement.

## Références croisées

- `batch-processing` — architecture commune (contrat de callbacks, points d'entrée menu/drop) ; identifie déjà ce flux comme le seul interactif du lot des 8.
- `comicvine-metadata-fetch` — la fenêtre ComicVine elle-même en mode `batch=True` (compteur, bouton Suivant, caches partagés), réutilisée et pilotée par cet orchestrateur.
- `comicinfo-metadata-editor` — `parse_comic_info_xml`/écriture du `ComicInfo.xml`, réutilisés pour charger/sauvegarder les métadonnées de chaque fichier.
- `zip-compression` — réglage appliqué à la réécriture du CBZ à la sauvegarde.
- `batch-library-create` — l'autre traitement batch qui, comme celui-ci, ne suit pas le pattern `_ThreadSignals`/`_ProgressDialog` standard des 6 flux de conversion/recompression.
