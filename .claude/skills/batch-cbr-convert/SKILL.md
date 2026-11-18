---
name: batch-cbr-convert
description: Localiser ou modifier la conversion par lot CBR→CBZ (dépendance rarfile). Utiliser dès qu'une tâche touche à batch_convert_cbr_to_cbz ou au traitement "Conversion en lot de fichiers CBR en CBZ".
---

# Conversion par lot CBR→CBZ — MosaicView

Un des 8 traitements par lot du projet (skill `batch-processing`, **à lire en premier** pour l'architecture commune : pattern confirm/progress/summary, contrat `batch_callbacks`, registre anti-GC `_active_batches`, deux points d'entrée menu/drop). Ce skill-ci détaille uniquement les spécificités du flux CBR — ne pas dupliquer ici ce qui est déjà couvert par le skill général.

Convertit récursivement tous les fichiers `.cbr` d'un dossier en `.cbz`, dans `modules/qt/batch_dialogs_qt.py` (`batch_convert_cbr_to_cbz` → `batch_convert_cbr_to_cbz_confirm` → `_run_cbr_conversion`).

## Dépendance bloquante — `rarfile`

**Seul flux à bloquer avant même le scan du dossier** si sa dépendance est absente : `batch_convert_cbr_to_cbz` (`batch_dialogs_qt.py`) vérifie `if rarfile is None` en tout premier, avant même d'ouvrir le sélecteur de dossier — `ErrorDialog` immédiat (`dialogs.batch_cbr.no_cbr_title`/`rarfile_unavailable`) si le module `rarfile` n'a pas pu être importé (`try: import rarfile except ImportError: rarfile = None`, en tête de `batch_dialogs_qt.py`). Contrairement à CB7 (dépend d'un binaire externe `7zip/`, jamais absent car embarqué) ou CBT (`tarfile` stdlib, toujours disponible).

## Détection du vrai format — `detect_archive_type`

Avant tout traitement, chaque fichier passe par `detect_archive_type(cbr_path)` (magic bytes, `archive_type_detector.py`) — un `.cbr` mal nommé qui est en réalité un ZIP, un 7z, un TAR ou un EPUB est **simplement renommé** vers l'extension de son format réel (`.cbz`, `.cb7`, `.cbt`, `.epub`) plutôt que "converti" :

```python
if real_type in ("CBZ", "CB7", "CBT", "EPUB"):
    label_map   = {...}   # « CBR → CBZ »… : texte de la barre de pages
    counter_map = {...}   # compteur de chaque format
    new_path = _rename_misnamed(cbr_path, real_type, claims, conversion_errors)
```

Le renommage passe toujours par `_rename_misnamed` (règle commune, jamais d'écrasement, nom libre « X (1).cbz »… réservé contre les autres lots ; skill `batch-processing`), jamais par un `os.rename` local. Les lots CB7 et CBT ont la même structure, avec leurs propres formats de redirection. Quatre compteurs séparés (`renamed_cbz`/`renamed_cb7`/`renamed_cbt`/`renamed_epub`), chacun affiché dans le résumé et écrit dans le journal seulement si `> 0`. Un format totalement inconnu (`real_type is None`) → erreur loggée (`"{basename}: format inconnu"`), fichier ignoré, pas de tentative de conversion en aveugle.

## Lecture de l'archive RAR — `rarfile.RarFile`

1. **Vignette** (avant la conversion réelle) : la première page de l'ouverture, `_emit_first_page_thumbnail` → `archive_members.first_page` (ordre de l'ouverture, taille contrôlée avant lecture, `MAX_MEMBER_SIZE` : une page géante n'est jamais chargée pour la vignette). Échec silencieux (vignette vide) — une vignette manquante n'interrompt jamais la conversion. Même aide pour les lots CB7 et CBT et la recompression.
2. **Conversion réelle** : relit l'archive une seconde fois (nouvel objet `RarFile`, pas de réutilisation du premier), liste **tous** les fichiers (pas seulement les images — un CBR peut contenir des `.nfo`/`ComicInfo.xml` à préserver), triés de la même façon. Archive vide (`total_pages == 0`) → erreur, fichier ignoré (aucun CBZ vide n'est créé).

## Écriture du CBZ — normalisation de mode couleur

L'archive est lue par le lecteur commun (`with open_archive(cbr_path, "CBR") as archive`, `all_files = archive.files()`, dans l'ordre de l'ouverture) et l'écriture passe par `_copy_pages_to_cbz(cbz, all_files, archive.read, lambda m: m.name, _on_page)` (`archive.read` → `_rar_read`, `archive_members.py` : refuse avant lecture un membre annoncé à plus de `MAX_MEMBER_SIZE`, page alors en échec et CBR gardé ; un nom porté par plusieurs fichiers du RAR, que rarfile ne sait pas distinguer, est lu par extraction du CBR et retrouvé par son contenu — skill `archive-image-loading`, « Lecture des membres »), boucle commune à CBR/CB7/CBT (skill `batch-processing`, section "Écriture du CBZ de sortie"), qui retourne les pages en échec. Pour chaque fichier de l'archive source, elle tente de l'ouvrir en PIL et passe la page à `_normalized_page_bytes(raw, img, ext)` (`batch_dialogs_qt.py`, commun aux lots CBR/CB7/CBT et IMG) : les modes non-standard (`CMYK`/`YCbCr`/`I`/`F`) sont ramenés à un mode courant par `image_ops._to_writable_mode` — CMYK converti en RGB **par son profil ICC** (`cmyk_to_rgb`), `I`/`F` remis à l'échelle 0-255 puis `L` au lieu d'être écrêtés (une conversion directe en RGB donne une page toute blanche pour des entiers 32 bits) — puis réencodés par l'encodeur commun `entries.encode_page` (skill `apply-image-operation`) dans le format de l'extension d'origine (`entries.page_format`, JPEG si extension inconnue), avec ce qui est propre aux lots : qualité 100, résolution de la page (`image_dpi`), métadonnées gardées **orientation EXIF comprise** (`keep_orientation=True` : les pixels ne sont pas tournés ici). L'encodeur applique le reste : profil ICC seulement s'il correspond au mode écrit (celui d'une page CMJN convertie ne l'est plus), compression TIFF d'origine sinon LZW, sous-échantillonnage JPEG de l'original. La page passe en mode courant **avant** l'encodage, même vers JPEG ou TIFF qui sauraient garder le CMJN : c'est le but de la normalisation, les lecteurs de BD affichant mal ces modes. Toute autre page est écrite telle quelle. **Si l'ouverture PIL échoue** (fichier non-image, `.nfo`, `ComicInfo.xml`, image dans un format que PIL ne sait pas décoder), le `raw` original est écrit **tel quel sans modification** (`except Exception: pass`, le bloc `try` englobe seulement la tentative de conversion, pas l'écriture) — un fichier non-image traverse donc la conversion intact, jamais perdu ni corrompu par une tentative de traitement image qui ne le concerne pas.

**Nom de chaque entrée du CBZ** : `_unique_flat_name(file_name, written_names)` — le CBZ est aplati (nom de fichier seul, sans dossier) et un membre homonyme d'un autre dossier reçoit un suffixe `_2`, `_3`… au lieu d'écraser/dupliquer l'entrée précédente. Détail et raison : skill `batch-processing`, section "Écriture du CBZ de sortie".

`gc.collect()` tous les 20 pages pendant l'écriture (limite le pic mémoire sur une grosse archive), et un second `gc.collect()` après la fermeture du ZIP.

## Suppression de la source — `is_permanent`

Si une page n'a pas pu être lue ou écrite (CRC invalide, archive protégée par mot de passe…), `_IncompleteCbz` est levée dans le bloc `_interruptible_zip_writer` : le CBZ incomplet ne prend jamais son nom, le CBR est conservé et `_log_incomplete_cbz` consigne l'erreur dans le journal ; le fichier n'est pas compté comme converti (skill `batch-processing`, "Suppression de la source — uniquement après une sortie complète").

Le CBR traité et le nom du CBZ à créer sont réservés auprès des autres lots (`_claim_file`, `claims.reserve`), et le lot entier est refusé si un autre lot traite déjà l'un de ses fichiers (skill `batch-processing`, « Un seul lot à la fois sur les mêmes fichiers »). **Piège propre à CBR** : rarfile rouvre l'archive à chaque page lue ; rien ne verrouille le CBR pendant la conversion, un autre traitement peut donc le supprimer entre deux pages.

Le CBZ est relu par `_interruptible_zip_writer` avant de prendre son nom (`cbz.expected_count = total_pages` posé dans le bloc, skill `batch-processing`, « Relecture du CBZ avant qu'il prenne son nom » : CRC de chaque fichier, nombre de fichiers égal au nombre de pages) ; s'il ne se relit pas correctement, `WrittenFileCheckError` est consignée, aucun CBZ n'est créé et le CBR est conservé. Sinon, après la sortie du `with` (archive RAR refermée, sans quoi Windows refuserait la suppression) : `converted_cbz.delete_source(cbz, cbr_path, permanent=is_permanent)` (skill `batch-processing`, « Écrire, relire, supprimer »), suppression définitive si la checkbox "suppression permanente" de `_ConfirmDialog` est cochée, sinon Corbeille Windows (`utils.send_to_trash`). Un échec de suppression (`del_err`) est consigné séparément (`"{basename}: "` + `messages.errors.batch_source_delete_failed`, avec `_failure_text(del_err)`), et `converted_count` est incrémenté après ce bloc même dans ce cas — la conversion est comptée comme réussie même si le fichier source n'a pas pu être supprimé, cohérent avec le fait que le vrai résultat (le CBZ, vérifié) existe bien à ce stade. Même enchaînement dans les lots CB7, CBT, PDF et images.

## Résumé — `_CbrSummaryDialog` (`batch_dialogs_qt.py`)

Même structure que les résumés CB7 et CBT, avec 4 compteurs de renommage possibles :
- Message principal : `complete_message` (pas d'erreur/renommage) ou `complete_message_errors` (avec `count`/`total`) — bascule dès qu'il y a **soit** des erreurs **soit** au moins un renommage (`has_renamed = any(data.get(k, 0) > 0 for k in (...))`).
- Un `QLabel` par compteur de renommage non nul (`renamed_cbz_count`/`renamed_cb7_count`/`renamed_cbt_count`/`renamed_epub_count`), créés dynamiquement dans `__init__` (pas dans `_retranslate`, donc figés si le dialogue reste ouvert — cohérent puisque les compteurs eux-mêmes ne changent jamais après la fin du traitement, seul le **texte** de ces labels suit `_retranslate()`).
- Lien cliquable vers le log si présent (erreurs et/ou renommages) — `_open_path` (`os.startfile` sur Windows), menu contextuel dédié (`setup_path_label_context_menu`, skill `qt-context-menus`).
- **Lancé par la Bibliothèque** (« Convertir en CBZ » sur plusieurs fichiers, skill `library`) : `batch_convert_cbr_to_cbz_confirm(…, directories=…, on_finished=…)`. Avec `on_finished`, ce résumé n'est pas affiché : `_end_batch` rend `(new_files, ("cbr", summary_data))` — CBZ créés et fichiers renommés — à la chaîne, qui montre un résumé unique pour tous ses lots (`_ChainSummaryDialog`, mêmes textes et compteurs de renommage, `_extra_lines`). Confirmation annulée ou lot refusé : `on_finished(None, None)`. Même chose pour les lots CB7 et CBT (skill `batch-processing`, « Lots lancés sur une liste de fichiers par la Bibliothèque »).

## Log d'erreurs — `Log_cbrtocbz_{timestamp}.txt`

Écrit dans `get_mosaicview_temp_dir()` (skill `temp-files`) uniquement si `conversion_errors` **ou** `renamed_entries` est non vide — contenu : compteurs globaux (fichiers traités, fichiers ignorés du dossier, convertis, une ligne « Renommés (CBR→CBZ) » par renommage qui a eu lieu, erreurs : skill `batch-processing`, « Journal lisible »), puis section "Renamed files" (liste `{ancien} → {nouveau}`) si des renommages, puis section "Error details" si des erreurs. Chemin donné par `unique_log_path(mosaicview_temp, "cbrtocbz", now)` (`temp_files.py`, skill `temp-files`) : timestamp à la minute (`%Y_%m_%d_%H_%M`), suffixé `_2`, `_3`… si un journal du même nom existe déjà (deux lots dans la même minute). Conservé à la fermeture de l'application, comme tous les journaux. Les autres lots (CB7, CBT, PDF, IMG, recompression, métadonnées) suivent la même règle.

## Comment étendre

- **Ajouter une redirection de renommage** (ex. détecter un futur format supplémentaire) : le format dans le test `real_type in (...)`, `label_map`/`counter_map` dans `do_conversion`, un nouveau compteur `renamed_xxx = [0]` et sa ligne de journal (boucle `renamed_kind` de `on_done`), sa valeur dans `summary_data`, son label dans `_CbrSummaryDialog.__init__`/`_retranslate` (et `has_renamed`), sa clé dans `_ChainSummaryDialog._extra_lines` et `_retranslate`, la clé `dialogs.batch_cbr.renamed_xxx_count` (46 langues), et son extension dans `misnamed_archives.EXTENSION_OF_KIND`.
- **Changer la politique de conversion de mode couleur** : dans `_copy_pages_to_cbz`, boucle commune aux lots CBR, CB7 et CBT (skill `batch-processing`, « Copie des pages CBR/CB7/CBT »), jamais dans `do_conversion`.
- Voir aussi la section "Comment ajouter un nouveau flux" du skill `batch-processing` — ce fichier CBR n'est **pas** le modèle recommandé pour copier un nouveau flux (le skill général recommande CBT, sans la dépendance `rarfile`).

## Pièges connus

- **Le blocage `rarfile is None` intervient avant le scan de dossier**, pas après — un flux copié depuis CBR pour un format sans dépendance externe ne doit pas reproduire ce garde-fou en tête de fonction.
- **4 compteurs de renommage possibles, pas 1** — le résumé et le journal affichent jusqu'à 4 lignes de renommage distinctes selon le format réel détecté (CB7 et CBT aussi). Un format oublié dans la liste n'est pas renommé : il est lu comme un RAR et finit en erreur (cas de l'EPUB avant qu'il y soit ajouté).
- **Ne jamais écrire une entrée du CBZ avec `os.path.basename(...)` direct** — deux pages homonymes dans des dossiers différents produiraient deux entrées ZIP de même nom, dont une seule lisible, alors que le CBR source est supprimé ensuite. Toujours `_unique_flat_name`.
- **Une image non décodable par PIL traverse la conversion sans modification** — ne pas supposer que toutes les entrées d'un CBZ de sortie sont passées par une normalisation de mode couleur.
- **`converted_count` incrémenté même si la suppression de la source échoue** — la réussite de la conversion et la réussite de la suppression sont deux états indépendants dans le comptage.
- **Timestamp de log à la minute près** — collision de nom de fichier log théoriquement possible entre deux lots très rapprochés.

## Références croisées

- `batch-processing` — architecture commune (pattern confirm/progress/summary/thread, contrat de callbacks, registre anti-GC, points d'entrée menu/drop) ; à lire avant ce skill-ci.
- `batch-cb7-convert` / `batch-cbt-convert` — les deux autres formats vers lesquels CBR peut rediriger un fichier mal nommé.
- `zip-compression` — réglage `zip_compression_kwargs`/niveau utilisateur appliqué à l'écriture du CBZ de sortie.
- `sort-images` — `natural_sort_key`, réutilisé ici pour trier les fichiers de l'archive comme pour la mosaïque.
- `temp-files` — `get_mosaicview_temp_dir()`, emplacement du log d'erreurs/renommages.
- `qt-context-menus` — menu contextuel du lien vers le log dans le résumé.
