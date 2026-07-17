---
name: bump-version
description: Bump le numéro de version de MosaicView dans les 4 fichiers (MosaicView.py, version_info.txt, index.html, CHANGELOG.md). À invoquer UNIQUEMENT sur demande explicite de l'utilisateur — jamais en réaction à une simple mise à jour du CHANGELOG.
---

# Bump de version — MosaicView

Quand on change le numéro de version (ex. 1.5.2 → 1.5.3), modifier **exactement ces 4 fichiers** (source de vérité : `Pour pousser vers GitHub.txt`, point 0) :

1. **`MosaicView.py`** : `__version__ = "x.y.z"` (près du haut du fichier).
2. **`version_info.txt`** : 4 occurrences — `filevers`, `prodvers` (format `x, y, z, 0`) et `FileVersion`, `ProductVersion` (format `"x.y.z.0"`).
3. **`index.html`** :
   - `<div class="badge" id="version-badge">vx.y.z ...</div>` (HTML brut)
   - `document.getElementById('version-badge').textContent = 'vx.y.z · Windows';` (JS, fin de fichier)
   - La plupart des langues utilisent un placeholder générique `"badge": "v… · Windows"` réécrit dynamiquement par le JS — NE PAS y toucher.
   - Seules exceptions : 3 langues fictives avec une valeur figée en dur au lieu du placeholder (`tlh-piqad`, `qya-tengwar`, `sjn-tengwar`) — à mettre à jour tant que cette incohérence existe.
4. **`CHANGELOG.md`** : ajouter (ou transformer une entrée déjà présente sans numéro) une entrée `## [x.y.z] - YYYY-MM-DD - <titre>` avec le contenu du changement.

## Procédure

1. Lire la version actuelle : `grep -n "x\.y\.z" MosaicView.py version_info.txt index.html` (remplacer par l'ancienne version).
2. Modifier les 3 premiers fichiers avec Edit (pas de script bash/python pour réécrire — toujours l'outil Edit).
3. Vérifier qu'il ne reste aucune occurrence de l'ancienne version : `grep -n "ancienne_version" MosaicView.py version_info.txt index.html` doit être vide.
4. Mettre à jour ou créer l'entrée CHANGELOG.md correspondante.

## Ce que ce skill NE fait PAS

- Ne commit pas, ne tag pas, ne push pas.
- Ne compile pas (PyInstaller).
- Ne crée pas la release GitHub.

Ces étapes suivantes du workflow de release (commit, tag, push, compilation, publication de la release GitHub) sont documentées dans `Pour pousser vers GitHub.txt` (points 1 à 5) mais sont **exclusivement manuelles, faites par l'utilisateur lui-même** — ce ne sera jamais Claude qui les exécute, même sur demande explicite.

## Piège historique

Premier bump 1.4.1→1.4.2 fait sans toucher `index.html` — toujours vérifier les 4 fichiers, pas seulement 3.
