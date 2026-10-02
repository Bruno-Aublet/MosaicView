---
name: batch-cb7-convert
description: Localiser ou modifier la conversion par lot CB7→CBZ (via le binaire 7z.exe embarqué). Utiliser dès qu'une tâche touche à batch_convert_cb7_to_cbz ou au traitement "Conversion en lot de fichiers CB7 en CBZ".
---

# Conversion par lot CB7→CBZ — MosaicView

Un des 8 traitements par lot du projet (skill `batch-processing`, **à lire en premier** pour l'architecture commune : pattern confirm/progress/summary, contrat `batch_callbacks`, registre anti-GC `_active_batches`, deux points d'entrée menu/drop). Ce skill-ci détaille uniquement les spécificités du flux CB7 — ne pas dupliquer ici ce qui est déjà couvert par le skill général.

Convertit récursivement tous les fichiers `.cb7` d'un dossier en `.cbz`, dans `modules/qt/batch_dialogs_qt.py` (`batch_convert_cb7_to_cbz` → `batch_convert_cb7_to_cbz_confirm` → `_run_cb7_conversion`, résumé `_Cb7SummaryDialog`). Structure quasi identique à `batch-cbr-convert` (mêmes 4 étapes, même normalisation de mode couleur, même log, même pattern de résumé à 3 compteurs de renommage) — ce skill se concentre sur ce qui diffère : la lecture de l'archive 7z.

## Pas de dépendance Python bloquante — binaire 7z.exe embarqué

Contrairement à CBR (bloque tout le flux si `rarfile` n'est pas importable), CB7 **ne vérifie aucune dépendance en tête de fonction** — le binaire `7zip/7z.exe` est embarqué dans le projet (voir skill `check-embedded-versions`), toujours présent, jamais une cause de blocage précoce comme pour `rarfile`.

## Lecture de l'archive — `_list_7z_files`/`_read_7z_file` (`archive_loader.py`)

**Différence fondamentale avec CBR/CBT** : pas d'objet "archive ouverte" réutilisable (`rarfile.RarFile`/`tarfile.TarFile`) — chaque opération relance un **processus externe** `7z.exe` séparé via `subprocess.run` :

- **`_get_7z_exe()`** : chemin vers le binaire embarqué, compatible PyInstaller (`sys._MEIPASS` si compilé, `os.path.abspath(".")` sinon) — `7zip/7z.exe` relatif à la racine de l'app.
- **`_to_short_path(path)`** : convertit le chemin Unicode de l'archive en chemin court **8.3 Windows** (`GetShortPathNameW`, API `ctypes`/`kernel32`) — nécessaire car `7z.exe` en ligne de commande peut mal gérer certains chemins Unicode/accentués ; fallback silencieux sur le chemin original si la conversion échoue.
- **`_list_7z_files(archive_path)`** : `7z.exe l -ba -slt -sccUTF-8 <chemin_court>`, parse le stdout ligne par ligne pour extraire les chemins complets des membres, normalisés en `/` (filtrés : fichiers uniquement, pas les dossiers). `-sccUTF-8` force une sortie UTF-8 : sans lui, 7z écrit dans la page de code console (OEM) et un nom accentué/non latin décodé en UTF-8 devient des `U+FFFD`, introuvable ensuite à l'extraction (page vide → marquée corrompue). Lève une exception si `returncode >= 2` (erreur fatale 7z — code 1 est un avertissement non bloquant, toléré).
- **`_read_7z_file(archive_path, member_name)`** : `7z.exe e -so <chemin_court> -- <chemin_complet>` — extraction **vers stdout** (`-so`), pas vers un fichier temporaire sur disque. Reçoit le chemin complet du membre (tel que renvoyé par `_list_7z_files`, `\` accepté aussi) et le transmet tel quel, **sans `-r`** : avec un nom seul + `-r`, 7z extrait tous les membres de même nom (`A/p.jpg` et `B/p.jpg`) concaténés sur stdout avec un code retour 0, et PIL décode silencieusement le premier — mauvaise image sans aucune erreur. Sans `-r`, un nom seul ne matche que le fichier à la racine de l'archive (et un nom inexistant renvoie 0 octet avec code retour 0).

**Conséquence de performance à connaître** : contrairement à CBR (une seule ouverture `RarFile`, puis lectures multiples en mémoire) et CBT (`tarfile` stdlib), CB7 lance **un nouveau processus `7z.exe` par fichier extrait** — un CB7 avec 200 pages lance environ 200 invocations de processus externe pendant sa conversion (plus une pour la vignette, plus une pour le listing initial). Coût non négligeable si un jour un profilage de performance batch est demandé — mais **c'est le comportement existant du projet**, pas un bug, ne pas "corriger" en réécrivant vers un mode d'extraction en lot sans consigne explicite.

## Détection du vrai format et redirection

Même mécanique que `batch-cbr-convert` (`detect_archive_type`), mais les 3 formats de redirection possibles sont **CBZ, CBR, CBT** (pas CB7 lui-même, puisqu'un fichier déjà détecté 7z reste dans le flux normal) — `renamed_cbz`/`renamed_cbr`/`renamed_cbt`, résumé à 3 compteurs conditionnels comme pour CBR (voir `batch-cbr-convert` pour le détail de cette UI, identique).

## Écriture du CBZ et suppression

Strictement identiques à `batch-cbr-convert` : normalisation `CMYK`/`YCbCr`/`I`/`F` → `RGB` avec fallback "écrit tel quel si PIL échoue à ouvrir", CBZ aplati avec noms homonymes suffixés `_2`, `_3`… via `_unique_flat_name` (skill `batch-processing`, section "Écriture du CBZ de sortie"), `gc.collect()` tous les 20 pages, collision de nom `.cbz` gérée par suffixe `" (N)"`, suppression source via `is_permanent`/`safe_delete_file` avec `converted_count` incrémenté indépendamment du succès de la suppression. La boucle est la fonction commune `_copy_pages_to_cbz`, avec `lambda f: _read_7z_file(cb7_path, f)` comme lecteur de page.

**Page en échec** : `_read_7z_file` lève une exception dès que 7z.exe sort avec un code non nul. C'est le cas de chaque page d'un CB7 chiffré sans chiffrement des noms : la liste des membres est lisible, mais aucune extraction ne réussit. `_copy_pages_to_cbz` retourne alors ces pages en échec, et `_discard_incomplete_cbz` supprime le CBZ et conserve le CB7 (skill `batch-processing`, "Suppression de la source — uniquement après une sortie complète"). Cas réel couvert par `tests/test_batch_copy_pages.py`.

## Log et résumé

`Log_cb7tocbz_{timestamp}.txt` (même structure que CBR), `_Cb7SummaryDialog` identique en structure à `_CbrSummaryDialog` (voir `batch-cbr-convert` pour le détail complet, transposable directement — seules les clés de traduction changent de préfixe `batch_cbr` → `batch_cb7`).

## Comment étendre

- **Changer le mode d'invocation de 7z.exe** (ex. batching des extractions pour réduire le nombre de processus lancés) : uniquement `_list_7z_files`/`_read_7z_file` dans `archive_loader.py` — **partagé** avec le chargement normal d'archives CB7 (skill `archive-image-loading`), pas propre à ce flux batch ; une modification ici affecte aussi l'ouverture normale d'un CB7 dans MosaicView, pas seulement la conversion par lot.
- Voir la section "Comment ajouter un nouveau flux" du skill `batch-processing` pour étendre à un nouveau format.

## Pièges connus

- **Un processus externe par fichier extrait**, pas une archive ouverte une fois — different de CBR/CBT ; à garder à l'esprit avant toute optimisation de performance de ce flux.
- **`_list_7z_files`/`_read_7z_file` sont partagées avec le chargement normal d'archives** (skill `archive-image-loading`) — une correction de bug ici doit être revalidée sur le chargement normal d'un CB7 aussi, pas seulement sur ce flux batch.
- **Chemin court 8.3 requis pour `7z.exe`** — si `_to_short_path` échoue silencieusement (retombe sur le chemin original), un chemin Unicode complexe peut faire échouer l'appel `subprocess` en aval sans message d'erreur clair à ce stade précis.
- **Toujours passer le chemin complet du membre à `_read_7z_file`, jamais un nom seul, et ne jamais y réintroduire `-r`** — voir ci-dessus : deux fichiers homonymes dans des dossiers différents donneraient la même image, sans erreur. Couvert par `tests/test_7z_member_extraction.py` (homonymes, racine vs sous-dossier, plusieurs niveaux, espaces, Unicode, `\`), qui exécute les vraies fonctions sur une vraie archive via le `7z.exe` embarqué (extraction AST, sans import Qt).
- **Code retour 1 de 7z toléré, `>= 2` seul est fatal** — ne pas durcir cette condition sans vérifier l'impact sur des archives légèrement non standard mais lisibles.

## Références croisées

- `batch-processing` — architecture commune (pattern confirm/progress/summary/thread, contrat de callbacks, registre anti-GC, points d'entrée menu/drop) ; à lire avant ce skill-ci.
- `batch-cbr-convert` — structure de résumé/log quasi identique (3 compteurs de renommage), à consulter pour le détail complet transposable ici.
- `batch-cbt-convert` — le troisième format de redirection possible pour un fichier `.cb7` mal nommé.
- `archive-image-loading` — `_list_7z_files`/`_read_7z_file`/`_get_7z_exe` réutilisées telles quelles pour le chargement normal (non-batch) d'une archive CB7.
- `check-embedded-versions` — version du binaire `7zip/7z.exe` embarqué, à vérifier avant une release.
- `zip-compression` — réglage appliqué à l'écriture du CBZ de sortie.
- `temp-files` — emplacement du log d'erreurs/renommages.
