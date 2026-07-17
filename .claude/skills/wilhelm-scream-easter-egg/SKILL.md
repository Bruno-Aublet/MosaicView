---
name: wilhelm-scream-easter-egg
description: Localiser ou modifier l'easter egg sonore du cri de Wilhelm (erreurs système rares et graves uniquement, jamais une validation utilisateur) et son export depuis l'aide. Utiliser dès qu'une tâche touche à easter_eggs_qt.py, play_wilhelm_scream, ou ErrorDialog(play_sound=...).
---

# Easter egg — le cri de Wilhelm — MosaicView

Petit clin d'œil sonore : certaines erreurs **rares et graves** déclenchent le célèbre [cri de Wilhelm](https://en.wikipedia.org/wiki/Wilhelm_scream), un effet sonore réutilisé dans des centaines de films et séries depuis les années 1950. Domaine public / CC0 (Wikimedia Commons).

## Règle centrale — rare et grave uniquement, jamais gênant

**Ne jamais déclencher ce son sur une validation utilisateur ordinaire** (champ vide, annulation, confirmation de fermeture, sélection invalide) — c'est explicitement documenté en tête de `easter_eggs_qt.py` et répété dans le texte d'aide utilisateur lui-même. Le son est réservé aux erreurs **système** imprévues et rares : échec d'écriture sur le disque, erreur réseau inattendue, ressource interne manquante — jamais aux garde-fous normaux du flux applicatif (sélection vide, nom de fichier invalide, etc.). L'objectif est de rester un easter egg occasionnel et amusant, pas un agacement récurrent — si une erreur peut raisonnablement se produire en usage normal (mauvaise manipulation, oubli), elle ne doit **pas** déclencher le son.

**Avant d'ajouter `play_sound=True` à un nouvel appel `ErrorDialog`, se poser la question : cette erreur peut-elle arriver souvent en usage normal, ou seulement dans un cas système exceptionnel ?** Dans le doute, ne pas l'activer — l'omission est sans conséquence, l'ajout à tort est ce qu'il faut éviter.

**Règle de comportement (CLAUDE.md)** : si la création ou la modification d'une fonction amène à créer une `ErrorDialog` pour ce genre d'erreur rare et grave, **proposer** à l'utilisateur d'y ajouter `play_sound=True` — jamais l'ajouter automatiquement sans demander, et ne pas proposer pour une simple validation utilisateur.

## Les 2 fichiers audio — `Sound/Wilhelm_Scream.wav` et `Sound/Wilhelm_Scream.ogg`

Deux formats du même son sont embarqués dans le dossier `Sound/` à la racine du projet :

- **`Sound/Wilhelm_Scream.wav`** — le format réellement **joué** par l'application (`easter_eggs_qt.py`, via `winsound.PlaySound`, API Windows qui ne lit que du WAV). C'est le fichier qui compte pour le déclenchement du son lui-même.
- **`Sound/Wilhelm_Scream.ogg`** — présent uniquement pour l'**export** (voir section dédiée plus bas) : proposé en téléchargement à l'utilisateur en plus du `.wav`, mais jamais lu directement par l'application elle-même.

Le texte d'aide (`locales/fr.json`, clé `help.wilhelm_scream_content`) précise explicitement que le son a été *"converti au format WAV pour être lu par l'application"* — le `.ogg` est donc une alternative de format offerte à l'export, pas une seconde source de lecture.

## Déclenchement — `easter_eggs_qt.py::play_wilhelm_scream()`

```python
def play_wilhelm_scream():
    try:
        winsound.PlaySound(_WILHELM_SCREAM_PATH,
                           winsound.SND_FILENAME | winsound.SND_ASYNC)
    except Exception:
        pass
```

- **`SND_ASYNC`** — lecture asynchrone, ne bloque jamais l'UI le temps que le son se termine.
- **Échec totalement silencieux** (`except Exception: pass`) — un périphérique audio absent, un fichier manquant, ou toute autre erreur de lecture ne doit **jamais** faire planter l'application ni perturber l'affichage de l'erreur réelle qui a déclenché l'easter egg ; l'utilisateur voit toujours son message d'erreur normalement même si le son ne joue pas.
- **`_resource_path()`** — résolution compatible PyInstaller (`sys._MEIPASS` si compilé, `os.path.abspath(".")` sinon), même pattern que les autres ressources embarquées du projet (icônes, polices).
- **`winsound`** — module stdlib **Windows uniquement** ; cohérent avec le fait que MosaicView est une application Windows (voir CLAUDE.md, contexte du projet).

## Point d'intégration — paramètre `play_sound` de `ErrorDialog`

Le son n'est **jamais** appelé directement depuis le code métier — il transite systématiquement par le paramètre `play_sound=True` de `ErrorDialog` (`modules/qt/dialogs_qt.py:276`, défaut `False`) :

```python
class ErrorDialog(QDialog):
    def __init__(self, parent, title, message, play_sound=False):
        ...
        if play_sound:
            from modules.qt.easter_eggs_qt import play_wilhelm_scream
            play_wilhelm_scream()
```

Le son est joué **à la construction du dialogue**, avant même l'affichage — l'utilisateur entend le cri au moment où la fenêtre d'erreur apparaît, pas après un délai.

### Sites d'appel actuels (`play_sound=True`)

Grep `play_sound=True` pour la liste exhaustive à jour plutôt que de la considérer figée, mais au moment de la rédaction de ce skill, une quinzaine de sites répartis dans :
- **`file_operations_qt.py`** (le plus grand nombre — échecs de sauvegarde/écriture CBZ, opérations fichier qui échouent de façon système).
- **`clipboard_qt.py`** — échec de copie d'archive complète vers le presse-papiers système.
- **`printing_qt.py`** — erreur d'impression (2 sites).
- **`web_import_qt.py`** — URL invalide ou échec réseau imprévu lors d'un import web.
- **`comicvine_update_check_qt.py`** — erreur lors de la vérification de mise à jour de métadonnées ComicVine.

Tous partagent le même profil : une opération qui échoue pour une raison **système** (disque, réseau, presse-papiers Windows, imprimante) plutôt qu'une saisie ou un choix utilisateur incorrect.

## Export du son — fenêtre d'aide

L'utilisateur peut télécharger les 2 fichiers audio depuis l'application elle-même, sans lien vers l'extérieur :

- **`export_wilhelm_scream(parent_widget)`** (`modules/qt/user_guide_qt.py:479`) : ouvre un sélecteur de dossier (`QFileDialog.getExistingDirectory`), copie (`shutil.copy2`) chacun des fichiers listés dans `_WILHELM_SCREAM_FILES = ["Wilhelm_Scream.ogg", "Wilhelm_Scream.wav"]` (les deux formats, dans cet ordre) depuis `resource_path("Sound", ...)` vers le dossier choisi. Chaque copie est individuellement protégée par un `try/except` — l'échec d'un des deux fichiers n'empêche pas la copie de l'autre.
  - Si au moins un fichier a été copié : `_show_success_dialog` (fenêtre de succès dédiée à l'export, réutilisée par d'autres exports de la fenêtre d'aide comme les icônes ou la police pIqaD) avec le compte de fichiers copiés et un lien vers le dossier/premier fichier.
  - Si aucun fichier trouvé sur disque : `ErrorDialog` (`messages.errors.file_not_found.title`/`messages.errors.no_wilhelm_scream_found`) — **sans** `play_sound=True` ici, cohérent avec la règle centrale : un fichier ressource manquant à l'export n'est pas le genre d'erreur système grave visée par l'easter egg lui-même (et jouer le son sur un échec d'export du son serait d'ailleurs un peu absurde).
- **Point d'entrée UI** : bouton dans la fenêtre d'aide (`user_guide_qt.py:884`), câblé via le dict de callbacks central de `MosaicView.py` (`"export_wilhelm_scream": lambda: export_wilhelm_scream(panel)`) — voir skill `user-guide` pour l'organisation générale de cette fenêtre.

## Traductions

`locales/fr.json` : `help.wilhelm_scream` (titre de la section, *"Le cri de Wilhelm"*), `help.wilhelm_scream_content` (explication complète, lien Wikipedia, mention de la conversion WAV), `help.wilhelm_scream_save` (libellé du bouton d'export), `help.wilhelm_scream_save_success` (message de succès avec `{count}`), `messages.errors.no_wilhelm_scream_found` (échec si fichiers introuvables sur disque). Voir skill `add-translation` pour ajouter/modifier une clé dans les ~47 langues du projet.

## Comment étendre

- **Ajouter un nouveau site de déclenchement** : passer `play_sound=True` à l'appel `ErrorDialog` concerné — **seulement** si l'erreur correspond au profil "système rare et grave" (voir règle centrale) ; ne jamais l'ajouter par réflexe à toute nouvelle `ErrorDialog`.
- **Changer le son lui-même** : remplacer les 2 fichiers dans `Sound/` en conservant les noms exacts (`Wilhelm_Scream.wav`/`Wilhelm_Scream.ogg`) et en vérifiant la licence (le fichier actuel est domaine public/CC0) — `_WILHELM_SCREAM_PATH` (`easter_eggs_qt.py`) et `_WILHELM_SCREAM_FILES` (`user_guide_qt.py`) référencent ces noms en dur, à mettre à jour ensemble si le nom de fichier change.
- **Ajouter un format supplémentaire à l'export** : ajouter l'entrée dans `_WILHELM_SCREAM_FILES` (`user_guide_qt.py:476`) et déposer le fichier correspondant dans `Sound/` — le `.wav` utilisé pour la lecture réelle (`_WILHELM_SCREAM_PATH` dans `easter_eggs_qt.py`) reste indépendant de cette liste, pas besoin de le modifier pour changer les formats proposés à l'export.

## Pièges connus

- **Ne jamais activer `play_sound=True` sur une validation utilisateur normale** — c'est la règle la plus importante de ce mécanisme, documentée à 3 endroits différents du code (docstring `easter_eggs_qt.py`, docstring `ErrorDialog`, texte d'aide utilisateur) : si une tâche demande d'ajouter une gestion d'erreur, ne pas ajouter le son par défaut sans réfléchir à sa pertinence.
- **Le `.ogg` n'est jamais lu par l'application**, seulement proposé à l'export — ne pas chercher un chemin de lecture `.ogg` dans le code, il n'existe pas (`winsound` ne sait de toute façon lire que du WAV).
- **`winsound` est Windows-only** — cohérent avec le projet, mais à garder à l'esprit si un jour une portabilité multi-OS est envisagée pour ce module spécifiquement.
- **Échec de lecture totalement silencieux** — ne pas ajouter de `MsgDialog`/notification en cas d'échec de `play_wilhelm_scream()`, ce serait contraire à l'esprit "ne doit jamais perturber l'affichage de l'erreur réelle".
- **L'échec de l'export lui-même ne déclenche pas le son** — cohérence volontaire, pas un oubli.

## Références croisées

- `user-guide` — organisation générale de la fenêtre d'aide où vit le bouton d'export ; `export_wilhelm_scream` suit le même schéma que les autres exports de cette fenêtre (icônes, police pIqaD via `_show_success_dialog` partagé).
- `add-translation` — clés de traduction `help.wilhelm_scream*` à maintenir dans les ~47 langues.
