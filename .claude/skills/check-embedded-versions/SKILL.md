---
name: check-embedded-versions
description: Vérifier les versions des logiciels embarqués dans MosaicView (7-Zip, UnRAR, PyMuPDF, Pillow) face aux dernières publiées (retard de sécurité). À invoquer sur demande explicite ou avant une compilation PyInstaller destinée à être distribuée.
---

# Vérification des versions des logiciels externes embarqués — MosaicView

## Pourquoi

Ces composants sont du code natif C/C++ qui parse des fichiers téléchargés non fiables (CBZ/CBR/CB7/PDF) : un bug mémoire dans l'un d'eux = exécution de code arbitraire potentielle au simple chargement d'un fichier. C'est la principale surface d'attaque de MosaicView, et **PyInstaller fige les versions au moment de la compilation** : un exe distribué embarque pour toujours les versions du jour du build. La seule façon de patcher les utilisateurs est de publier une nouvelle release. D'où cette vérification avant chaque compilation distribuée.

Historique de CVE réelles sur ces composants : UnRAR CVE-2022-30333 (RCE exploitée contre Zimbra), 7-Zip CVE-2024-11477 / CVE-2025-53816/53817 (corruption mémoire parsing RAR, corrigées en 25.00), libwebp CVE-2023-4863 (0-day exploité, touche Pillow via .webp), MuPDF (flux continu de bugs mémoire PDF).

## Les 4 composants et où ils vivent

| Composant | Emplacement | Utilisé pour |
|---|---|---|
| 7-Zip (`7z.exe` + `7z.dll`) | `7zip/` (racine du repo, embarqué tel quel dans l'exe) | ouverture des CB7 |
| UnRAR (`UnRAR.exe`) | `unrar/` (racine du repo, embarqué tel quel) | ouverture des CBR via `rarfile` |
| PyMuPDF | package pip dans `.venv` | ouverture des PDF (workers multiprocess) |
| Pillow | package pip dans `.venv` | décodage de chaque image de chaque page |

## Procédure

### 0. Découverte : y a-t-il de nouveaux composants embarqués depuis le dernier audit ?

La liste des 4 composants ci-dessus est celle de l'audit de référence — elle peut être devenue incomplète. Avant de vérifier les versions, chercher les nouveaux venus :

```bash
# Binaires natifs à la racine du repo non couverts par la liste (aujourd'hui : 7zip/, unrar/)
ls */*.exe */*.dll 2>/dev/null

# Dépendances pip réellement installées dans l'environnement de build
.venv/Scripts/python.exe -m pip list
```

Croiser avec `requirements.txt` (liste de référence des dépendances du projet). Chercher parmi les résultats toute bibliothèque **avec du code natif qui décode des données externes** (archives, images, PDF, vidéo, audio, polices, XML...) absente du tableau ci-dessus. Si un nouveau composant est trouvé : l'auditer comme les autres **et proposer de l'ajouter au tableau et à l'état de référence de ce skill** (le skill doit se maintenir lui-même, sinon il répondra "tout est à jour" en ignorant le nouveau venu).

### 1. Versions actuellement présentes

```bash
# Binaires embarqués (afficher la bannière de version)
./7zip/7z.exe i | head -3          # ex. "7-Zip 24.08 (x64) ... 2024-08-11"
./unrar/UnRAR.exe | head -2        # ex. "UNRAR 7.11 x64 freeware"

# Packages pip de l'environnement de build/exécution
.venv/Scripts/python.exe -m pip show PyMuPDF Pillow | grep -E "^(Name|Version)"
```

Lancer `7z.exe`/`UnRAR.exe` sans argument est sans danger (affichage d'aide/bannière uniquement).

**Piège vécu** : le repo a contenu deux environnements — `.venv` (le vrai, premier dans le PATH, celui qui compile) et un vieux `venv/` sans point, reliquat tkinter avec Pillow 9.5.0, supprimé le 2026-07-10. Toujours vérifier les versions dans **`.venv`** (et vérifier avec `where pyinstaller` que c'est bien lui qui gagne dans le PATH si un doute existe). Ne pas se fier à `requirements.txt` : il exprime des minima, pas ce qui est réellement installé.

### 2. Dernières versions publiées — recherche web OBLIGATOIRE à chaque invocation

**Règle absolue, sans exception : ne jamais répondre sur les dernières versions publiées sans avoir, PENDANT CETTE INVOCATION, effectivement appelé WebSearch/WebFetch.** La section "État de référence" en bas de ce fichier est un historique du dernier audit, PAS une source de vérité sur l'état actuel — elle se périme dès le lendemain (nouvelles versions, nouvelles CVE). L'avoir lue ne remplace jamais la recherche web. Si une session a répondu "tout est à jour" sans appeler WebSearch/WebFetch dans cette même conversation, c'est une erreur : le piège vécu est réel et s'est déjà produit une fois (audit répondu comme "à jour" sans vérification réelle, corrigé le 2026-07-10).

Chercher sur le web (WebSearch/WebFetch), à chaque fois, même si l'état de référence semble récent :
- 7-Zip : https://www.7-zip.org/ (page d'accueil affiche la dernière version stable)
- UnRAR : https://www.rarlab.com/rar_add.htm et https://www.win-rar.com/whatsnew.html?L=0 (le numéro UnRAR CLI suit celui de WinRAR/RAR — croiser les deux pages, la première ne donne pas toujours un numéro exploitable)
- PyMuPDF : https://pypi.org/project/PyMuPDF/
- Pillow : https://pypi.org/project/pillow/

Vérifier aussi systématiquement si des CVE sont corrigées entre la version embarquée et la dernière, via une recherche WebSearch dédiée (ex. "<composant> CVE <année>", "<composant> security fix changelog") — ne pas se contenter de la page de téléchargement, les CVE importantes (RCE, path traversal, exploitation active) sont souvent absentes du changelog court et nécessitent une recherche séparée.

### 3. Rapport

Présenter un tableau version embarquée / dernière version / état (✅ récent, ⚠️ en retard), et pour tout retard, dire si des correctifs de sécurité sont concernés (un retard avec CVE de parsing corrigée entre-temps = mise à jour à recommander fermement ; un retard purement fonctionnel = à signaler simplement).

### 4. Mise à jour (uniquement sur accord de l'utilisateur)

- **7-Zip** : remplacer `7zip/7z.exe` et `7zip/7z.dll` par ceux d'une installation standard (Program Files\7-Zip). Garder `7zip/license.txt`.
- **UnRAR** : remplacer `unrar/UnRAR.exe` par celui de rarlab (UnRAR for Windows, version ligne de commande). Garder `unrar/license.txt`.
- **PyMuPDF / Pillow** : `.venv/Scripts/python.exe -m pip install -U pymupdf pillow`.
- **Cohérence `requirements.txt`** : si la mise à jour corrige une CVE (ou si une version minimale est requise pour être sûr), relever le minimum correspondant dans `requirements.txt` (ex. passer `Pillow>=10.0` à `Pillow>=X`) — sinon une installation depuis les sources sur une autre machine peut satisfaire `requirements.txt` tout en retombant sur une version vulnérable. Ne pas y toucher pour une mise à jour purement fonctionnelle.
- Après mise à jour : re-tester l'ouverture d'un CB7, d'un CBR, d'un PDF et d'un CBZ contenant du WebP (l'utilisateur exécute les tests, jamais lancer l'appli soi-même).
- La mise à jour n'atteint les utilisateurs finaux qu'à la prochaine release compilée.

## Piège vécu à ne pas reproduire

Le 2026-07-10, un audit a répondu "tout est à jour" sans avoir réellement appelé WebSearch/WebFetch pendant l'invocation — erreur corrigée le jour même (voir règle "recherche web OBLIGATOIRE" ci-dessus). Ne jamais se fier à un état mémorisé : toujours revérifier sur le web à chaque invocation, même si le dernier audit date d'hier.
