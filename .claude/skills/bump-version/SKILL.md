---
name: bump-version
description: Bump le numéro de version de MosaicView dans les 4 fichiers (MosaicView.py, version_info.txt, index.html, CHANGELOG.md), via le script bump_version.py pour les 3 premiers. À invoquer UNIQUEMENT sur demande explicite de l'utilisateur — jamais en réaction à une simple mise à jour du CHANGELOG.
---

# Bump de version — MosaicView

Quand on change le numéro de version (ex. 1.5.2 → 1.5.3), **4 fichiers** portent le numéro (source de vérité : `RELEASE.txt`, point 0a) :

1. **`MosaicView.py`** : `__version__ = "x.y.z"` (près du haut du fichier).
2. **`version_info.txt`** : 4 occurrences — `filevers`, `prodvers` (format `x, y, z, 0`) et `FileVersion`, `ProductVersion` (format `"x.y.z.0"`).
3. **`index.html`** :
   - `<div class="badge" id="version-badge">vx.y.z &middot; Windows</div>` (HTML brut)
   - `document.getElementById('version-badge').textContent = 'vx.y.z · Windows';` (JS, fin de fichier)
   - La plupart des langues utilisent un placeholder générique `"badge": "v… · Windows"` réécrit dynamiquement par le JS — NE PAS y toucher.
   - Seules exceptions : 3 langues fictives avec une valeur figée en dur au lieu du placeholder (`tlh-piqad`, `qya-tengwar`, `sjn-tengwar`) — à mettre à jour tant que cette incohérence existe.
4. **`CHANGELOG.md`** : entrée `## [x.y.z] - YYYY-MM-DD - <titre>`, toujours rédigée à la main (règles dans `CLAUDE.md`, section « Ce que Claude ne fait jamais lui-même »).

## Le script `bump_version.py` (racine du dépôt)

Change le numéro dans les 3 premiers fichiers, jamais dans le CHANGELOG. **C'est l'utilisateur qui le lance** (ouvrir le fichier dans VSCode, ▶️ Run) : il affiche la version actuelle (lue dans `__version__`), demande la nouvelle, puis :
- refuse une saisie qui n'a pas la forme `x.y.z` (un `v` initial est toléré) ou égale à la version actuelle ; demande confirmation pour une version inférieure ;
- calcule les 3 fichiers modifiés en mémoire, chaque emplacement repéré par une expression régulière avec un nombre d'occurrences attendu (1 pour `__version__`, 2 + 2 pour `version_info.txt`, 1 pour le badge HTML, 1 pour la ligne JS, au moins 0 pour les badges figés des traductions) ; **si un seul emplacement manque, aucun fichier n'est écrit** et la liste des emplacements introuvables est affichée ;
- écrit en UTF-8 avec `newline=""` (fins de ligne CRLF conservées), affiche le nombre de remplacements par fichier, signale si l'ancien numéro apparaît encore dans l'un d'eux, et rappelle l'entrée du CHANGELOG à rédiger.

Les messages du script n'utilisent que des caractères de cp1252 : une console Windows en cp1252 plante (`UnicodeEncodeError`) sur un caractère hors de ce jeu (flèche « → » par exemple).

**Modifier le script** : un nouvel emplacement du numéro de version (nouveau fichier, nouvelle ligne figée dans `index.html`) s'ajoute comme un appel à `_replace(texte, motif, remplacement, nombre_attendu, libellé, errors)` dans `main()`, avec le nombre exact d'occurrences attendu (ou `(minimum,)`) — sans ça, le script ne le met pas à jour et ne le signale qu'à travers l'avertissement « apparaît encore ». Mettre aussi à jour la liste des fichiers ci-dessus et `RELEASE.txt`.

## Si l'utilisateur demande à Claude de faire le bump

1. Lire la version actuelle : `grep -n "x\.y\.z" MosaicView.py version_info.txt index.html` (remplacer par l'ancienne version).
2. Modifier les 3 premiers fichiers avec Edit — Claude ne lance pas `bump_version.py` lui-même (règle `CLAUDE.md` : jamais de script pour réécrire un fichier ; le script est l'outil de l'utilisateur).
3. Vérifier qu'il ne reste aucune occurrence de l'ancienne version : `grep -n "ancienne_version" MosaicView.py version_info.txt index.html` doit être vide.
4. Mettre à jour ou créer l'entrée CHANGELOG.md correspondante.

**Piège** : `index.html` s'oublie facilement — toujours vérifier les 4 fichiers, pas seulement les 2 fichiers Python/ressource.

## Ce que ce skill NE fait PAS

- Ne commit pas, ne tag pas, ne push pas.
- Ne compile pas (PyInstaller).
- Ne crée pas la release GitHub.

Ces étapes suivantes du workflow de release (tests, commit, tag, push, compilation, publication de la release GitHub) sont documentées dans `RELEASE.txt` mais sont **exclusivement manuelles, faites par l'utilisateur lui-même** — ce ne sera jamais Claude qui les exécute, même sur demande explicite.
