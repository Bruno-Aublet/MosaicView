---
name: app-registration
description: Localiser ou modifier la déclaration silencieuse de MosaicView dans le registre Windows (liste « Ouvrir avec » de l'Explorateur, extensions déclarées SupportedTypes). Utiliser dès qu'une tâche touche à app_registration_qt.py, ensure_app_registered, ou à l'association de fichiers.
---

# Déclaration silencieuse dans le registre — MosaicView

Au démarrage, MosaicView s'inscrit lui-même dans le registre Windows (HKCU, aucun droit admin) pour apparaître dans la liste « Ouvrir avec » de l'Explorateur, sous le nom « MosaicView ». C'est une **déclaration de visibilité** (« cette appli existe et sait ouvrir ces types »), PAS une association de fichiers : le choix de l'appli par défaut au double-clic reste entièrement un geste manuel de l'utilisateur (voir la section « Pourquoi on ne peut pas aller plus loin »).

## Fichiers clés

- **`modules/qt/app_registration_qt.py`** (~80 lignes) — tout le mécanisme : une seule fonction publique `ensure_app_registered()`, aucun autre module ne touche à ces clés.
- **`MosaicView.py::main()`** — le point d'appel unique, juste **après** le check single instance (voir skill `single-instance`) : seule l'instance qui reste réellement ouverte exécute la déclaration, jamais un processus redirigé qui va `sys.exit(0)` aussitôt.

## Ce qui est écrit (HKCU uniquement, jamais HKLM)

Branche unique `HKCU\Software\Classes\Applications\MosaicView.exe` :

| Clé/valeur | Contenu | Effet |
|---|---|---|
| `shell\open\command` → `(default)` | `"<exe>" "%1"` | L'inscription proprement dite : fait apparaître MosaicView dans « Ouvrir avec » |
| `FriendlyAppName` (sur la racine) | `MosaicView` | Nom affiché sans le `.exe` |
| `SupportedTypes` → une valeur vide par extension | `.mvdb`, `.cbz`, `.cbr`, `.cb7`, `.cbt`, `.epub`, `.pdf`, `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`, `.bmp`, `.tiff`, `.tif`, `.ico`, `.avif` | Mise en avant dans les suggestions « Ouvrir avec » de ces types |

C'est exactement la branche que Windows crée lui-même quand un utilisateur navigue manuellement jusqu'à un exe via « Ouvrir avec → Choisir une autre application » — on la crée juste proactivement.

## Quand et comment ça s'exécute

- **À chaque démarrage**, mais **uniquement en mode compilé** (`getattr(sys, 'frozen', False)`) — jamais en mode développement (`python MosaicView.py` depuis VS Code n'écrit rien).
- **Idempotent** : lit d'abord l'état ; si la commande pointe déjà vers `sys.executable` courant, que `FriendlyAppName` est bon et que toutes les extensions de `_SUPPORTED_EXTS` sont présentes → aucune écriture. Sinon tout est réécrit. Ça répare automatiquement le cas « l'utilisateur a déplacé le dossier MosaicView » (la commande enregistrée pointait vers un chemin mort).
- **`SHChangeNotify(SHCNE_ASSOCCHANGED)`** après écriture (via `ctypes`) pour que l'Explorateur rafraîchisse « Ouvrir avec » sans redémarrage. L'Explorateur peut quand même mettre quelques secondes/un F5 à refléter le changement.
- **Silencieux par construction** : tout est sous `try/except` englobant — un échec (poste d'entreprise verrouillé par GPO, etc.) ne perturbe jamais le lancement et n'affiche rien.
- Le module utilise uniquement `winreg` et `ctypes` (stdlib) — **aucun impact sur les specs PyInstaller**, rien à embarquer.

## Ajouter/retirer une extension déclarée

Modifier le tuple `_SUPPORTED_EXTS` en tête de `app_registration_qt.py`. Deux règles :

1. **La liste est exhaustive aux yeux de Windows** — piège vérifié en vrai : quand `SupportedTypes` existait avec seulement `.mvdb`, MosaicView a **disparu** du menu immédiat « Ouvrir avec » des `.cbz`/`.cbr`/etc. (Windows interprète « je déclare ce que je sais ouvrir » comme « je ne sais PAS ouvrir le reste »). Toute extension que l'appli sait ouvrir doit donc y figurer — en pratique, garder la liste alignée sur `_COMIC_EXTS` de `MosaicView.py::main()` (+ `.mvdb`). Si on ajoute un nouveau format d'archive/image à l'appli (voir skill `archive-image-loading`), penser à l'ajouter ici aussi.
2. **Le code ne supprime jamais rien** : retirer une extension du tuple ne la retire pas du registre des machines où elle a déjà été écrite (le check d'idempotence ne vérifie que la présence des extensions attendues, pas l'absence d'extensions en trop). Pour un retrait effectif il faudrait une suppression explicite — à signaler à l'utilisateur si le cas se présente.

## Sécurité (audit du 2026-07-16 — rien à corriger, propriétés à ne pas casser)

Le mécanisme a été audité et jugé sain. Les propriétés suivantes sont **délibérées** — toute modification doit les préserver :

- **HKCU uniquement, jamais HKLM, aucune élévation** : impossible d'affecter les autres comptes ou le système.
- **Chemins quotés** : `expected = f'"{exe}" "%1"'` — les deux paires de guillemets sont **indispensables**. Sans guillemets autour de l'exe : détournement classique par chemin non quoté (`C:\Program.exe` intercepte `C:\Program Files\...`). Sans guillemets autour de `%1` : un nom de fichier avec espaces serait découpé en plusieurs arguments. Pas de problème d'échappement : le caractère `"` est interdit dans les noms de fichiers NTFS.
- **Aucune donnée contrôlée par un tiers n'entre dans le registre** : chemin exe = `sys.executable`, extensions = tuple codé en dur. Pas de surface d'injection — à maintenir si on ajoute des valeurs.
- **Auto-réparation incidemment défensive** : si un malware même-compte réécrit la commande vers son exe, le prochain lancement de MosaicView la réécrit vers le bon chemin (le check d'idempotence compare à `sys.executable` courant).
- **Limite assumée « dernière copie lancée gagne »** : la branche pointe vers le dernier exe compilé lancé, y compris une copie trojanisée exécutée une fois par l'utilisateur. Inhérent au principe d'auto-déclaration (Windows fait pareil au choix manuel d'un exe), non corrigeable sans installeur signé — ne pas chercher à « fixer » ça.

## Pourquoi on ne peut PAS aller plus loin (association automatique impossible)

Recherché en profondeur (juillet 2026) — ne pas re-proposer, ne pas re-tenter :

- Le **choix par défaut** au double-clic vit dans `HKCU\...\Explorer\FileExts\<ext>\UserChoice`, protégé par un **hash cryptographique non documenté** (SID utilisateur + appli + timestamp, et depuis 2025 un identifiant machine — variante `UserChoiceLatest`).
- Depuis mars 2024, le **driver noyau UCPD** (User Choice Protection Driver) bloque en plus toute écriture de ces clés par un processus utilisateur, même avec un hash valide. Les outils historiques (SetUserFTA) sont morts.
- Les **trois API Win32 prévues pour ça sont neutralisées depuis Windows 10** : `IApplicationAssociationRegistrationUI::LaunchAdvancedAssociationUI` (ne fait plus rien), `SHOpenWithDialog` avec `OAIF_ALLOW_REGISTRATION`/`OAIF_REGISTER_EXT` (flags ignorés, la case « Toujours utiliser » n'apparaît plus par cette voie), écriture directe du registre (bloquée, voir ci-dessus).
- **Personne n'y échappe** : Photoshop, GIMP, Edge lui-même passent tous par le même entonnoir — leur bouton « définir par défaut » ouvre l'UI Windows et l'utilisateur fait le dernier clic. Leur seul avantage est un installeur qui fait la déclaration de visibilité en `HKLM` à l'installation.
- Le seul chemin qui écrit encore `UserChoice` : le geste manuel de l'utilisateur dans l'UI native (clic droit → Ouvrir avec → Choisir une autre application → cocher « Toujours utiliser »). Notre déclaration sert précisément à ce que MosaicView soit visible et bien nommé à ce moment-là.
- Un projet de **bouton « Associer les .mvdb » dans le menu Base de données a été étudié puis abandonné** pour cette raison (un bouton qui aboutit à « allez cliquer vous-même » n'apporte rien) — ne pas le ressusciter sans nouvelle demande explicite de l'utilisateur.

## Interactions et pièges

- **Lien avec le single instance** (skill `single-instance`) : les deux mécanismes sont voisins dans `main()` et complémentaires — la déclaration rend l'association manuelle facile, le single instance fait que le fichier double-cliqué s'ouvre dans l'instance déjà ouverte. L'ordre est important : single instance d'abord, déclaration ensuite.
- **`UserChoice` existant pointant sur `Applications\MosaicView.exe`** : quand l'utilisateur a associé une extension via « Ouvrir avec », son `UserChoice` a souvent pour ProgId `Applications\MosaicView.exe` — c'est-à-dire notre branche. Supprimer la branche **casse alors le double-clic** de cette extension ; la recréer (au prochain lancement de l'exe) le répare automatiquement, le hash `UserChoice` restant valide.
- **Plusieurs copies de l'exe** (ex. `C:\MosaicView\` installée vs `dist\` de build) : chaque copie lancée réécrit la commande vers **son propre** chemin — « MosaicView » dans « Ouvrir avec » désigne toujours la dernière copie lancée en mode compilé.
- **Comportement capricieux du menu immédiat** : Windows décide seul d'afficher un sous-menu immédiat « Ouvrir avec » ou la grande fenêtre de choix, selon l'historique de l'extension (`OpenWithList`, `UserChoice` présent/absent). Constaté : menu immédiat pour `.cbz`/`.cbr`, fenêtre pour `.mvdb` fraîchement désassocié — pas un bug de notre code, pas actionnable.
- **Test/debug** : vérifier l'état avec `Get-ItemProperty 'HKCU:\Software\Classes\Applications\MosaicView.exe\shell\open\command'` (et `\SupportedTypes`). Pour repartir de zéro : `reg export` de la branche en sauvegarde, puis `Remove-Item -Recurse` — le prochain lancement de l'exe compilé recrée tout. Se souvenir que le double-clic Explorateur teste la **copie installée** (celle du registre), pas celle de `dist\` (piège documenté aussi dans le skill `single-instance`).

## Ce qui n'est PAS géré ici

- L'écriture de `UserChoice` / l'association par défaut — impossible, voir section dédiée.
- L'ouverture des fichiers double-cliqués dans l'instance existante — skill `single-instance`.
- La Bibliothèque et le format `.mvdb` lui-même — skill `library`.
