---
name: update-checker
description: Localiser ou modifier la vérification de mise à jour de MosaicView depuis GitHub Releases (bandeau au démarrage, vérification manuelle du menu À propos). Utiliser dès qu'une tâche touche à update_checker_qt.py, check_for_updates_on_startup, ou à la comparaison de version.
---

# Vérification de mise à jour de l'application — MosaicView

Vérifie si une nouvelle version de **MosaicView lui-même** est disponible sur GitHub Releases — pas à confondre avec `comicvine_update_check_qt.py` (skill `comicvine-metadata-fetch`), qui vérifie les mises à jour de métadonnées **ComicVine**, un sujet totalement différent malgré le nom de fichier presque identique. Un seul fichier : `modules/qt/update_checker_qt.py`.

## Source de données

`_RELEASES_API = "https://api.github.com/repos/Bruno-Aublet/MosaicView/releases/latest"` — API GitHub Releases, requête HTTP brute via `urllib.request.urlopen` (pas de dépendance HTTP tierce). `_TIMEOUT = 5` secondes. Comparaison de version via la lib `packaging.version.Version` (comparaison sémantique correcte, pas une comparaison de chaînes) — `_is_newer(latest, current)` retourne `False` silencieusement si l'une des deux chaînes n'est pas parsable (`except Exception: return False`), donc une version mal formée ne déclenche jamais une notification erronée mais peut aussi masquer une vraie mise à jour si le tag GitHub est malformé.

`_normalize(tag)` retire juste le préfixe `v` (`"v1.0.1" → "1.0.1"`) — la version courante de l'application est lue via `MosaicView.__version__` (import différé `import MosaicView as _main`, pour éviter un import circulaire au chargement du module).

## Deux flux distincts, un seul module

### 1. Vérification manuelle — `check_for_updates_qt(parent)` + `_UpdateDialog`

Point d'entrée appelé depuis le menu À propos (`callbacks.get("check_for_updates")`, voir skill `menu-bar`). Ouvre `_UpdateDialog`, non-modal, qui :
1. Affiche immédiatement `updates.checking` ("Vérification...") pendant que la requête réseau tourne dans un `threading.Thread` daemon (`_fetch`).
2. Le thread réseau **ne touche jamais aux widgets Qt directement** — il émet un signal (`_ResultSignal.ready`, `QObject` dédié) avec `(status, latest_tag)`, traversée thread-safe standard vers le thread Qt principal.
3. Trois états d'affichage (`_retranslate`, rejouée à chaque résultat ET à chaque changement de langue) : `"ok"` (à jour, pas de bouton), `"update"` (bouton "Télécharger" vert, ouvre la page Releases dans le navigateur via `webbrowser.open`), `"error"` (bouton "Réessayer", relance `_fetch` dans un nouveau thread).
4. Centrage custom dans `showEvent` — **pas** `_center_on_widget` standard : recalcule manuellement la position pour la contraindre à l'écran courant (`QApplication.screenAt`/`primaryScreen`, clampé à `availableGeometry()`), pattern un peu différent du helper habituel `position_dialog_on_parent`/`_center_on_widget` documenté dans les règles CLAUDE.md — à vérifier avant de copier ce fichier comme modèle de centrage pour une nouvelle fenêtre.

### 2. Vérification automatique au démarrage — `check_for_updates_on_startup(main_window)`

Appelée une fois au lancement de l'application (voir `MosaicView.py`, point d'appel hors de ce fichier). Lance sa propre requête réseau dans un thread séparé (indépendant de `_UpdateDialog`, pas de code partagé au-delà de `_fetch_latest_release`/`_is_newer`/`_normalize`) :
- **Silencieuse en cas d'erreur ou si à jour** (`except Exception: pass`) — contrairement à la vérification manuelle, aucun message n'est jamais affiché à l'utilisateur pour ces deux cas ; seule une mise à jour trouvée produit un effet visible.
- Si une mise à jour est trouvée : `_on_startup_update_found` appelle **deux** méthodes sur `main_window` si elles existent (`hasattr` gardé, pas d'erreur si absentes) :
  - `main_window.show_update_banner(latest, release_title)` — bandeau visuel (voir ci-dessous).
  - `main_window.set_update_available_in_menu(latest)` — met à jour l'état consulté par le menu À propos (voir "Intégration menu" ci-dessous).
- `_startup_sig` (référence module-level) garde le `QObject` signal vivant — sans cette référence externe, le signal pourrait être détruit par le GC Python avant que la réponse réseau n'arrive (même risque que documenté pour les `QThread` dans le skill `pdf-loading`, ici appliqué à un simple `QObject` porteur de signal).

## Intégration avec `MainWindow` (`panel_widget.py`)

- **`show_update_banner(latest, release_title)`** (`panel_widget.py:515`) — construit un bandeau (`QWidget`/`QHBoxLayout`) sous la barre d'onglets, avec un label et un bouton de téléchargement (`webbrowser.open` direct vers la page Releases, indépendamment du bouton équivalent dans `_UpdateDialog`). Idempotent : si `self._update_banner is not None`, ne réaffiche rien (`return` immédiat) — un second appel (ex. si `check_for_updates_on_startup` était relancée) ne duplique pas le bandeau.
- **`set_update_available_in_menu`** — stocke la version disponible (`self._update_latest` sur `MainWindow`), consultée par `_populate_about_menu` (`menubar_qt.py:509`, voir skill `menu-bar`) : si non vide, le libellé "Vérifier les mises à jour" devient "Mise à jour disponible (vX.Y.Z)" **en gras** (`_get_current_font(9, bold=True)`), sinon le libellé standard s'affiche.
- `panel_widget.py:670-672` propage `_update_latest` de `main_window` vers le dict de callbacks (`wrapped["_update_latest"] = latest`) consommé par `_populate_about_menu`.

## Comment modifier

- **Changer la fréquence/déclenchement de la vérification au démarrage** : ce fichier ne contrôle pas *quand* `check_for_updates_on_startup` est appelée — chercher le point d'appel dans `MosaicView.py` (hors périmètre de ce fichier).
- **Changer l'URL du dépôt GitHub** (fork, renommage) : `_RELEASES_API`/`_RELEASES_PAGE`, constantes en tête de fichier — les deux doivent rester cohérentes (même dépôt), et le bouton de téléchargement de `show_update_banner` (`panel_widget.py`) a sa **propre** URL en dur à mettre à jour séparément (pas de constante partagée entre les deux fichiers).
- **Ajouter un nouvel état d'affichage** (ex. "version bêta disponible") : `_UpdateDialog._retranslate`, ajouter une branche `elif self._status == "...":` suivant le pattern existant (afficher/masquer les 2 boutons optionnels selon le cas).
- **Changer le timeout réseau** : `_TIMEOUT` (5 secondes), utilisé par `_fetch_latest_version`/`_fetch_latest_release` uniquement — n'affecte pas le fait que le thread reste daemon (ne bloque jamais la fermeture de l'app même si la requête traîne au-delà).

## Pièges connus

- **Ne pas confondre avec `comicvine_update_check_qt.py`** — nom de fichier presque identique, sujet totalement différent (vérification des métadonnées ComicVine vs vérification de MosaicView lui-même). Une recherche "update check" doit vérifier lequel des deux fichiers est réellement concerné avant de modifier quoi que ce soit.
- **`_is_newer` masque silencieusement une version GitHub malformée** — si le tag de la release GitHub ne suit pas le format `packaging.version.Version` attendu (ex. faute de frappe dans un tag), la fonction renvoie `False` (pas de mise à jour détectée) sans aucune trace ni log ; un déploiement dont personne n'est notifié doit faire vérifier le format exact du tag en premier lieu.
- **Centrage custom dans `_UpdateDialog.showEvent`, différent du helper standard** — ne pas copier ce bloc comme modèle pour une nouvelle fenêtre sans vérifier s'il ne vaudrait pas mieux utiliser `_center_on_widget`/`position_dialog_on_parent` (le pattern documenté dans les règles CLAUDE.md) ; cet écart existait déjà avant la création de ce skill, à signaler si une tâche future touche ce fichier.
- **La vérification au démarrage et la vérification manuelle ne partagent aucun état** — lancer une vérification manuelle depuis le menu À propos pendant que la vérification de démarrage est encore en cours crée deux requêtes réseau indépendantes ; pas de verrou ni de déduplication.
- **`_startup_sig` module-level n'est jamais explicitement libéré** — reste vivant jusqu'à la fin du process (pas un problème pratique, une seule vérification par lancement d'application, mais à garder en tête si ce module devait un jour appeler `check_for_updates_on_startup` plusieurs fois dans une même session).

## Références croisées

- `menu-bar` — `_populate_about_menu`, libellé "Vérifier les mises à jour"/"Mise à jour disponible" selon `callbacks["_update_latest"]`.
- `comicvine-metadata-fetch` — `comicvine_update_check_qt.py`, sujet voisin par le nom mais indépendant (mise à jour des métadonnées d'un comic, pas de l'application).
