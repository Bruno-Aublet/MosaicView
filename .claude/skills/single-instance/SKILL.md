---
name: single-instance
description: Localiser ou modifier le mécanisme single instance de MosaicView (redirection des lancements suivants via named pipe, ouverture des fichiers double-cliqués dans l'instance existante). Utiliser dès qu'une tâche touche à single_instance_qt.py ou au lancement d'une seconde instance.
---

# Single instance — MosaicView

MosaicView est une application **mono-instance stricte** : quel que soit le mode de lancement (icône, association de fichier Windows, ligne de commande), si une instance tourne déjà, le nouveau processus lui transmet son éventuel fichier et se termine immédiatement. L'instance existante remonte au premier plan et ouvre le fichier reçu.

## Fichiers clés

- **`modules/qt/single_instance_qt.py`** — tout le mécanisme de transport (client + serveur), ~140 lignes, aucun autre module ne touche au canal.
- **`MosaicView.py::main()`** — les deux points de branchement :
  - **côté client** (~ligne 906, juste après `QApplication(sys.argv)`) : `try_forward_to_running_instance(_argv_path or "")` → si `True`, `sys.exit(0)` immédiat, **avant** le splash et la construction de `MainWindow` (pas de flash de fenêtre) ;
  - **côté serveur** (~ligne 1063, bloc "Ouverture via association de fichier Windows") : `_open_associated_path(path)` (aiguillage partagé), `_on_forwarded_path(path)` (handler serveur : remontée au premier plan + aiguillage), `start_single_instance_server(_on_forwarded_path)`.

## Comment ça marche

Canal local Qt (`QLocalServer`/`QLocalSocket` de **QtNetwork** — named pipe sur Windows) au nom fixe :

```
MosaicView-SingleInstance-<USERNAME>
```

Le suffixe `%USERNAME%` isole les sessions Windows d'une même machine. 

1. **Tout lancement** tente d'abord `QLocalSocket.connectToServer(...)` avec `waitForConnected(500)`.
2. **Connexion réussie** → handshake : le client **attend la bannière** `_BANNER` (`MOSAICVIEW1\n`) que le vrai serveur envoie dès la connexion, pendant `_HANDSHAKE_TIMEOUT_MS` max (3 s, volontairement large : un serveur au thread principal occupé peut tarder, conclure trop vite lancerait une 2e instance à tort).
   - **Bannière absente/incorrecte** → celui qui écoute n'est pas MosaicView (pipe squatté ou collision de nom) : `abort()` + `return False` — l'appli démarre normalement, son `listen()` échouera → mode dégradé (voir 3).
   - **Bannière reçue** → le client délègue son droit de premier plan **au seul PID du serveur** (`GetNamedPipeServerProcessId` sur `sock.socketDescriptor()` puis `AllowSetForegroundWindow(pid)` ; repli `-1`/ASFW_ANY si le PID n'est pas identifiable), envoie `sys.argv[1]` en UTF-8 (chaîne vide si lancement sans fichier), puis quitte.
3. **Connexion échouée** → on est la première instance : `QLocalServer.removeServer(...)` (nettoie un canal orphelin post-crash) puis `listen(...)`. Si `listen()` échoue quand même : **mode dégradé silencieux** — `_server = None`, l'appli fonctionne normalement mais sans single instance.
4. **Réception côté serveur** : à chaque connexion entrante, le serveur **écrit d'abord la bannière** (`write(_BANNER)` + `flush()`), puis lecture asynchrone par signaux (`readyRead` accumule dans un buffer, `disconnected` déclenche le traitement) — **aucun blocage de l'event loop Qt** (règle non-modale de CLAUDE.md respectée : pas de `waitForReadyRead` côté serveur). Le buffer est **plafonné à `_MAX_MESSAGE_BYTES`** (128 Ko, couvre le plus long chemin Windows possible en UTF-8) : au-delà, la connexion est coupée sans traiter le message — en déconnectant `disconnected` de `_done` **avant** `abort()`, car `abort()` peut émettre `disconnected` et déclencherait sinon le callback sur un message tronqué. Le callback `on_path_received(path)` s'exécute dans le thread Qt principal.

## Ce que fait l'instance existante à la réception (`_on_forwarded_path` dans `MosaicView.py`)

1. Remonte la fenêtre principale : dé-minimise (`setWindowState(... & ~Qt.WindowMinimized | Qt.WindowActive)`) + `show()`/`raise_()`/`activateWindow()`.
2. Aiguille le chemin via `_open_associated_path(path)`, **le même** que celui du démarrage à froid :
   - chemin vide ou fichier inexistant → rien (simple remontée au premier plan) ;
   - `.mvdb` → `open_library_window(parent_panel=win._panel)` + `lib._action_open_db(path)` — voir skill `library` (la fenêtre Bibliothèque est un singleton jamais détruit, `open_library_window` gère déjà le re-affichage/raise ; `_action_open_db` ferme proprement la base courante avant d'ouvrir la nouvelle) ;
   - extension dans `_COMIC_EXTS` (CBZ/CBR/CB7/CBT/EPUB/PDF/images) → `win._panel._load_files([path])` sur **panel1** — voir skills `archive-image-loading` (routeur `_load_files`) et `panels`. Comportement identique à un drop : si une archive est déjà ouverte, la fusion est proposée (`import_merge_qt`).

Au démarrage à froid (pas d'instance existante), le même `_open_associated_path` est appelé différé de 200 ms (`QTimer.singleShot`) après la construction de la fenêtre.

## Sécurité (durcissements du 2026-07-16 — ne pas les défaire)

Modèle de menace : le pipe n'est accessible qu'aux processus **du même compte Windows** (descripteur de sécurité par défaut de `QLocalServer` sans `WorldAccessOption`). À l'intérieur d'un même compte il n'existe **aucune frontière de sécurité** possible — un processus malveillant local peut de toute façon injecter/lire la mémoire de MosaicView. Les durcissements ci-dessous ferment donc le raisonnable, pas l'impossible :

- **Bannière de handshake** (`_BANNER`) : elle **identifie, n'authentifie pas**. Son but unique est anti-DoS : sans elle, un squatteur du nom de pipe rendait MosaicView impossible à démarrer (chaque lancement lui donnait son chemin et mourait) ; avec elle, le pire cas devient « l'appli démarre, single instance désactivé ». La bannière est publique par construction (le serveur la donne à quiconque se connecte) — **inutile de chercher à la cacher/chiffrer**, et l'authentification réelle du serveur est structurellement impossible entre processus d'un même compte (pas de secret partageable qu'un voisin ne puisse lire ; DPAPI déchiffrable par tout le compte). L'usurpation/fuite des chemins ouverts reste possible et assumée.
- **`_MAX_MESSAGE_BYTES`** : borne anti-épuisement mémoire (un client qui streame des Go dans le pipe).
- **`AllowSetForegroundWindow(pid ciblé)`** : ne délègue plus le droit de premier plan à n'importe quel processus (ASFW_ANY), seulement au serveur vérifié — et seulement **après** la bannière.
- Défenses préexistantes côté traitement (dans `MosaicView.py`, inchangées) : le chemin reçu n'est jamais exécuté ni passé à un shell — `os.path.isfile()` + whitelist d'extensions + chargeurs internes uniquement ; `decode("utf-8", "replace")` robuste aux octets malformés.

## Pièges connus (vécus)

- **Transition de versions (handshake)** : un **ancien client** (pré-handshake) vers un **nouveau serveur** fonctionne (il envoie son chemin sans attendre la bannière, le serveur le lit). L'inverse — **nouveau client** vers **ancien serveur** (qui n'envoie pas de bannière) — conclut « imposteur » après 3 s et démarre une 2e instance. Cas transitoire de développement uniquement. Plus sournois : un exe installé antérieur au mécanisme complet ne parle pas du tout au pipe et démarre en double — vérifier la date de l'exe sur C: avant de conclure à un bug (voir piège « bonne copie de l'exe »).
- **Splash visible sur un lancement redirigé : normal.** Le splash PyInstaller est affiché par le **bootloader** avant toute ligne de notre Python (décompression ONE_FILE, imports PySide6) ; le check single instance est déjà au plus tôt possible dans notre code, mais après ces étapes. Le splash **Qt** (ligne ~980 de `MosaicView.py`), lui, n'apparaît jamais pour un lancement redirigé. Optimisation « check avant les gros imports » : refusée explicitement par l'utilisateur — ne pas la proposer.

- **PyInstaller / QtNetwork** : les deux specs (`MosaicView_ONE_DIR.spec`, `MosaicView_ONE_FILE.spec`) font une collecte PySide6 **ciblée** (liste `_qt_used`) avec une liste `excludes` explicite. `PySide6.QtNetwork` doit être dans `_qt_used` et **absent** des `excludes`, et `Qt6Network.dll` doit figurer dans la liste manuelle `_qt_core_dlls`. Si on l'oublie, l'exe compilé **crashe au lancement** (`ModuleNotFoundError: No module named 'PySide6.QtNetwork'`). C'est aujourd'hui la **seule** utilisation de QtNetwork du projet (~2,7 Mo dans le bundle) — ne pas la retirer des specs sans supprimer ce mécanisme.
- **Tester avec la bonne copie de l'exe** : l'association Windows pointe vers la copie **installée** (ex. `C:\MosaicView\MosaicView.exe`), pas vers `dist\`. Un double-clic teste la copie installée — si elle est antérieure à la modification testée, on croit à tort que le mécanisme est cassé. Vérifier la cible réelle dans le registre (`HKCU:\...\Explorer\FileExts\.mvdb\UserChoice` → ProgId → `HKCU:\Software\Classes\<ProgId>\shell\open\command`) et comparer les dates des exe avant de conclure. Après recompilation, recopier **tout le build** (dossier `_internal` inclus en ONE_DIR), pas seulement l'exe.
- **Test sans compiler** : le mécanisme marche à l'identique en Python pur et croise même les mondes (script ↔ exe, même pipe). Lancer l'instance 1 depuis VS Code, puis dans un second terminal `python MosaicView.py "chemin\vers\fichier.mvdb"` → le second processus doit se terminer aussitôt et l'instance 1 ouvrir le fichier. Mais le test « double-clic Explorateur » réel passe par l'exe associé (voir piège précédent).
- **Ordre dans `main()`** : le check client doit rester immédiatement après la création du `QApplication` (les `QLocalSocket` synchrones fonctionnent sans event loop qui tourne, mais nécessitent l'application Qt) et **avant** splash/`MainWindow` — le déplacer plus tard réintroduit un flash de fenêtre pour les lancements redirigés.
- **`_load_worker` de la Bibliothèque** : si la Bibliothèque est en plein chargement d'une base, `_action_open_db` ignore silencieusement la nouvelle demande (comportement existant de `library_window.py`, pas propre au single instance).

## Comment le modifier

- **Ajouter une extension ouvrable par double-clic** : l'ajouter à `_COMIC_EXTS` dans `MosaicView.py::main()` (et à l'aiguillage de `_open_associated_path` si le traitement diffère) — le transport, lui, ne change pas : il transmet n'importe quel chemin.
- **Transmettre autre chose qu'un chemin** (ex. plusieurs fichiers, une commande) : le protocole actuel est brut — « tout le contenu du message = un chemin ». Pour l'étendre, définir un format (ex. une ligne par fichier) en modifiant l'encodage côté `try_forward_to_running_instance` et le décodage dans `_done()` côté serveur, puis adapter `_on_forwarded_path`.
- **Désactiver le single instance** (retour au multi-instances) : retirer les deux branchements dans `main()` ; penser alors à retirer QtNetwork des specs (voir piège PyInstaller).
- **Debug** : les processus redirigés vivent < 1 s et l'exe est fenêtré (prints invisibles). Pour diagnostiquer, réintroduire temporairement des traces écrites dans un fichier **à la racine de `%TEMP%`** (PAS dans `%TEMP%/MosaicViewTemp`, nettoyé par `cleanup_all_temp_files` à la fermeture de l'appli et des fichiers), avec le pid dans chaque ligne — c'est l'absence de traces du second pid qui a permis de repérer le piège « mauvaise copie d'exe » ci-dessus.

## Ce qui n'est PAS géré ici

- L'**enregistrement** de l'association de fichiers Windows (`.mvdb` → MosaicView.exe) : le choix par défaut (`UserChoice`) reste un geste manuel de l'utilisateur (« Ouvrir avec »), verrouillé par Windows. En revanche, l'appli **se déclare** elle-même dans le registre au démarrage pour être visible dans la liste « Ouvrir avec » (`app_registration_qt.py`, appelé dans `main()` juste **après** le check single instance — l'ordre compte : un processus redirigé qui va mourir ne doit pas écrire le registre) — voir skill `app-registration`.
- Le comportement d'ouverture proprement dit (Bibliothèque, chargement d'archive, fusion) : voir skills `library` et `archive-image-loading` — ce skill s'arrête à l'aiguillage.
- Le process fils PDF (multiprocess, voir mémoire `project_pdf_support.md`) n'est pas concerné : les enfants `spawn` ne ré-exécutent pas `main()` (protégé par `freeze_support()` et `__name__ == "__main__"`).
