---
name: add-translation
description: Ajoute ou modifie des clés de traduction dans les locales de MosaicView (locales/*.json, ~47 fichiers dont 6 langues fictives) ou dans le bloc JS d'index.html. À invoquer pour traduire de nouvelles clés dans toutes les langues du projet.
---

# Ajout de traductions — MosaicView

**Deux systèmes de traduction distincts dans ce projet, à ne pas confondre :**
- `locales/*.json` — l'application Qt (`_()` / `_wt()`).
- Objet JS inline `const translations = {...}` dans `index.html` — la page web (landing page), totalement séparé, aucune clé partagée avec `locales/*.json`. Voir section dédiée [Traductions dans index.html](#traductions-dans-indexhtml) plus bas.

## Ordre et méthode

1. **Toujours commencer par le français** : ajouter la ou les nouvelles clés d'abord uniquement dans `locales/fr.json`, puis attendre que l'utilisateur teste lui-même que tout fonctionne (jamais Claude — ne jamais lancer l'application ni instancier Qt soi-même). **Ne jamais enchaîner automatiquement sur la traduction des autres langues** — attendre une autorisation explicite de l'utilisateur avant de passer au reste des langues.
2. **Lister `locales/`** avant d'écrire le script pour avoir la liste exacte des fichiers cibles — ne jamais supposer quelles langues existent (le nombre exact change, vérifier à chaque fois plutôt que de se fier à un total mémorisé).
   - **Interdiction absolue de deviner la liste de mémoire**, même partiellement (ex. ajouter "ru" par réflexe parce que la plupart des projets multilingues l'ont — ce projet n'a pas de `ru.json`). Le dictionnaire de traductions du script doit être construit à partir de la sortie réelle d'une commande de listing, jamais recopié depuis un total retenu d'une session précédente.
   - **Un `ls`/`Glob` déjà affiché plus tôt dans la même conversation, pour un autre besoin, ne compte pas comme cette vérification** — piège vécu (2026-08-09) : la liste des fichiers avait été affichée quelques messages plus tôt pour lire le format d'un fichier existant, puis le dictionnaire de traductions a quand même été écrit de mémoire sans la recroiser, provoquant un crash à mi-script (langue `ru` inexistante) après écriture de 21 fichiers sur 42, nécessitant un diagnostic et une reprise partielle. Toujours relancer le listing **juste avant** d'écrire le dictionnaire `TRANSLATIONS`, jamais réutiliser une liste vue plus haut dans la conversation.
   - Une fois la liste obtenue, construire l'ensemble exact des clés attendues (tous les fichiers sauf `fr.json`, `language_names.json`, et les 3 variantes CSUR si elles sont régénérées par script plutôt que traduites à la main) et comparer cet ensemble à celui du dictionnaire écrit **avant** d'exécuter le script — pas après un premier échec.
3. **Traduction par script Python** : écrire un script Python à la racine du projet (ex. `update_xxx_translations.py`) contenant toutes les traductions, avec `Write`, puis l'exécuter avec `Bash`. Pas d'agents, pas d'outils de traduction externes. Pas d'icônes ni d'emojis dans les traductions.
4. **TOUJOURS `json.loads`/`json.dumps`** pour lire/écrire les fichiers JSON de locale — jamais d'édition texte brute.
5. **Pas de fallback** : toutes les langues doivent être réellement traduites ; l'anglais (ou toute autre langue) ne sert jamais de traduction de secours pour une langue non traitée.
6. Les valeurs contenant des retours à la ligne doivent être de vrais `\n` Python (`.replace("\\n", "\n")` si besoin lors d'un copier-coller).
7. **zh-CN et zh-TW** : deux-points demi-chasse `:` (ASCII), jamais pleine chasse.

## Langues fictives (6 fichiers, 3 langues × 2 variantes)

Chaque langue fictive existe en version latine et en alphabet natif (encodage CSUR, Private Use Area — pas d'Unicode officiel pour klingon/tengwar). **Ne jamais traduire directement dans la variante alphabet natif** — toujours partir de la version latine et reconvertir via script :

| Langue | Fichier latin | Fichier alphabet natif | Police | Script de conversion |
|---|---|---|---|---|
| Klingon | `tlh.json` | `tlh-piqad.json` (CSUR pIqaD, U+F8D0–U+F8FF) | `fonts/pIqaD-qolqoS.ttf` | `conversion_tools/convert_piqad_csur.py` |
| Sindarin | `sjn.json` | `sjn-tengwar.json` (CSUR Tengwar, U+E000–U+E07F) | `fonts/AlcarinTengwarVF.ttf` | `conversion_tools/convert_tengwar_csur.py` (mode `sindarin-tengwar-general_use`) |
| Quenya | `qya.json` | `qya-tengwar.json` (CSUR Tengwar, U+E000–U+E07F) | `fonts/AlcarinTengwarVF.ttf` | `conversion_tools/convert_tengwar_csur.py` (mode `quenya-tengwar-classical`) |

- Les deux scripts se lancent directement (pas d'argv) : ils relisent tout le fichier source et régénèrent tout le fichier cible en une passe (le script Tengwar appelle Glaemscribe en Node.js via subprocess).
- **Piège** : ne jamais reconvertir un fichier CSUR sur lui-même (double conversion, résultat corrompu). Toujours reconvertir depuis la source latine (`tlh.json`, `sjn.json`, `qya.json`).
- **Piège paramètres** : `{size}`, `{width}`, etc. doivent être préservés intacts par le script de conversion (`re.split(r'(\{[^}]+\})', text)`) — sinon un caractère de `{size}` peut être converti en plein milieu du paramètre.
- **Clés exclues de la conversion** (restent en romanisation latine dans les deux fichiers CSUR) : `app_title`, `app_baseline` (top-level), et `window_title`, `quality_window_title`, `icons_window_title` à toute profondeur — ces clés sont lues directement en latin par `_wt()`.
- **Ordre de conversion pIqaD critique** : les digraphes doivent être traités avant leurs lettres composantes — `tlh` > `ch` > `gh` > `ng` > lettres simples.
- **`tengwar_guni_*` charsets** : produisent des codepoints hors de la plage d'Alcarin Tengwar (ex. U+EC53) — ne jamais les utiliser, rester sur `tengwar_freemono`.
- **Terminal Windows cp1252** : ne peut pas afficher les codepoints PUA (pIqaD/Tengwar) — pour vérifier une valeur convertie, utiliser `json.dumps(valeur)`, jamais `print()` direct (affiche des `?` ou plante).
- **Titres de fenêtres Windows — `_wt(key)`** (`modules/qt/localization.py`) : Windows ne peut pas afficher les polices CSUR dans la barre de titre (boîtes vides). Quand la langue courante est `tlh-piqad`, `sjn-tengwar` ou `qya-tengwar`, `_wt()` lit la valeur depuis la locale latine de base (`tlh`, `sjn`, `qya` via `_LATIN_FALLBACK`) au lieu de la CSUR. Tous les `setWindowTitle()` dans `modules/qt/` utilisent `_wt()` au lieu de `_()` — importer `from modules.qt.localization import _, _wt`. Voir skill `fonts` pour le mécanisme complet de sélection de police (`get_current_font()`) associé à ces mêmes 3 langues CSUR.
- Table de correspondance CSUR complète (pIqaD) et détails Glaemscribe (charsets, architecture JS) : voir les mémoires `project_piqad_csur` et `project_tengwar_csur` si besoin d'un historique plus complet.
- **Guillemet droit `"` toujours retiré à la conversion, jamais préservé** : `to_piqad()` (`convert_piqad_csur.py`) et `split_preserve()` (`convert_tengwar_csur.py`) suppriment systématiquement le caractère `"` du texte avant transcription — ni la police pIqaD (aucun glyphe pour ce caractère) ni la police Tengwar (glyphe `quotedbl` présent mais mal calibré, bien plus grand que les autres lettres) ne le rendent correctement à l'écran. Les fichiers latins (`tlh.json`/`sjn.json`/`qya.json`) gardent leurs guillemets normalement, seule la sortie CSUR en est dépourvue. Aucun caractère de guillemet alternatif (« », " ") n'est mieux pris en charge par ces polices — ne pas essayer d'en substituer un à la place si un nouveau cas apparaît ; formuler la phrase source sans guillemets si la citation d'un nom d'élément UI est nécessaire.

## Vérification qualité klingon/elfique/gallois

- **Obligatoire à chaque ajout de nouvelles clés en tlh/sjn/qya** : vérifier que les traductions ne contiennent pas de mots étrangers résiduels (mots gallois, anglais, français, ou une AUTRE langue fictive du projet — ex. du quenya glissé dans sjn — glissés par erreur au lieu du klingon/sindarin/quenya construit).
- **Vigilance particulière sur le sindarin (sjn)** : plusieurs audits antérieurs (2026-07-05 gallois, 2026-07-11 anglais) ont été annoncés comme concluants mais n'ont PAS éliminé le problème — un audit du 2026-07-12 a encore trouvé du gallois, du quenya mélangé, et un vocabulaire technique jamais stabilisé (jusqu'à 5 mots différents pour le même concept, ex. "série"). **Ne plus jamais annoncer une correction gallois/anglais comme définitive pour sjn** — le risque de résidu reste réel à chaque nouvelle clé.
- **Lexique sindarin obligatoire** : avant toute nouvelle traduction en sjn, consulter la mémoire `reference_sjn_sindarin_glossary` (série=Lú, éditeur=Tirhor, résumé=Hannas, métadonnées=Nothrannath, bibliothèque=Thamb, dossier=taur, image=randir/rendir, annuler=Haedh, undo=Avedui, redo=Adrevedui, supprimer=Deleb, coller=Pado, copier=Honom, télécharger=racine Sad-, appliquer=Orthertha, cliquer=Nedhoro). Si le concept y figure, réutiliser EXACTEMENT ce mot — ne jamais en improviser un autre. Si un concept technique n'y figure pas et qu'il existe déjà sous plusieurs formes dans `sjn.json`, s'arrêter et signaler la divergence plutôt que d'en ajouter une 6e variante silencieusement ; sinon choisir un mot et l'ajouter à ce lexique après coup.
- **Lexique quenya obligatoire** : même règle pour qya, consulter `reference_qya_quenya_glossary` (série=Lindelë, éditeur=Antanor, résumé=Parmë, métadonnées=Métanótë, bibliothèque=Parmavorn, dossier=alda/aldassë, image=fána/fánar, annuler=Avahaila, undo=Nurta, redo=Ata-carë, coller=Pata, copier=Samna, oui/non=Nai/Lá). qya.json a aussi montré des résidus d'anglais brut (pas juste du vocabulaire mal choisi) et un mot klingon glissé par erreur — vérifier au-delà du seul choix de vocabulaire.
- **Lexique klingon obligatoire** : même règle pour tlh, consulter `reference_tlh_klingon_glossary` (série=tetlh, éditeur=HevleH, métadonnées=mung qaw, bibliothèque=nIqHom, image=cha'nob/cha'nobmey, undo=qIl, redo=vI'ang, coller=lan, cancel=Qaw', oui/non=HIja'/ghobe'). "Résumé" et "dossier/répertoire" restent volontairement NON tranchés dans ce lexique (aucun mot existant assez fiable) — ne pas deviner, soit choisir un nouveau mot et l'ajouter au lexique après coup, soit demander. **Piège spécifique vérifié sur tlh** : un mot candidat fréquent peut déjà être pris pour un AUTRE concept ailleurs dans le fichier (`ghItlh` semblait bon pour "série" vu son usage dans `comicinfo.field_series`, mais signifie "page/fichier" dans 93 autres clés) — toujours vérifier les autres usages du mot avant de le retenir pour un nouveau concept, pas seulement compter ses occurrences.
- Uniquement sur les **nouvelles clés ajoutées** — ne pas rouvrir un audit complet des fichiers existants à chaque fois, SAUF si l'utilisateur demande explicitement un audit dédié.
- **Scan résidus anglais obligatoire après TOUT travail touchant tlh/sjn/qya** : lancer un scan regex de mots anglais courants sur les valeurs modifiées/ajoutées (en excluant `{params}`, noms propres MosaicView/ComicVine/ComicInfo/rarfile, et les faux positifs légitimes : sindarin `and` = hauteur/long, klingon `not` = jamais). Regex de base : `\b(inspired|by|the|of|with|for|from|file|files|all|save|should|module|application|images|default|normal|use|app|current|...)\b`. **Why** : le 2026-07-11, l'utilisateur a découvert « inspired by » dans `comicvine.credit` sjn/qya malgré un audit gallois antérieur qui ne visait pas l'anglais — purge complète de ~24 valeurs. Ne plus jamais annoncer une correction comme définitive sans ce scan.
  - Vocabulaire anglais déjà établi (2026-07-11), à réutiliser au lieu d'improviser : sjn fichier=bâr/bair, images=rendir, page=tell ; qya fichier=parma, image=emma, enregistrer=hep-, utiliser=yuhta-, appli=tamma ; tlh fichier=De'wI', convertir=ngor.
- **Moment de la vérification : sur le script Python, avant de l'exécuter** — relire les valeurs tlh/sjn/qya écrites dans les variables du script, corriger directement dans le script si un mot suspect apparaît, puis exécuter une seule fois. Éviter le cycle écrire→exécuter→relire le JSON→corriger→réexécuter.

## Traduction arménien (hy)

- **Ne jamais écrire les traductions arméniennes à la main sous forme d'escapes `\uXXXX`** dans le script.
- Écrire le texte arménien directement comme une vraie chaîne Unicode en clair dans la variable Python (copier-coller depuis une source fiable), puis laisser `json.dumps(..., ensure_ascii=False)` gérer l'encodage.
- **Piège** : une escape manuelle mal tapée comme `\u566` (3 chiffres) + lettre `a` fusionne en un caractère chinois (U+566A) — syntaxiquement valide, sémantiquement faux, indétectable à l'œil.
- **Vérification obligatoire après écriture** :
  ```python
  bad = [hex(ord(c)) for c in v if ord(c) > 0x058F and ord(c) not in (0x20, 0x3A, 0x2E)]
  assert not bad, f"Caractères non-arméniens : {bad}"
  ```

## Vérification finale après tout ajout de clés (locales/*.json)

1. Comparer les clés du bloc ajouté dans chaque langue à celles du français (aucune manquante, aucune en trop).
2. Valider que le JSON de tous les fichiers modifiés se parse sans exception (`json.load`).
3. Pour tlh/sjn/qya (et leurs variantes CSUR) : vérification qualité décrite ci-dessus.

## Traductions dans index.html

`index.html` (landing page) a son propre objet JS inline `const translations = { "fr": {...}, "en": {...}, ... }`, appliqué au DOM via `el.textContent = t[key]` (voir le script en fin de fichier). **Ce n'est ni du JSON, ni lié à `locales/*.json`** — mêmes langues (46 blocs : les mêmes codes que `locales/` moins `language_names.json`, donc les 40 langues naturelles + tlh/qya/sjn + leurs 3 variantes CSUR), mais des clés et un fichier différents.

Deux scripts génériques dans `conversion_tools/` prennent en charge l'insertion mécanique (ne contiennent aucune traduction en dur) :

### 1. `conversion_tools/insert_index_html_keys.py` — insertion des valeurs naturelles + fictives latines

Moteur importable, pas un script à lancer seul. Depuis un script d'appel ponctuel (à écrire à chaque ajout, comme les `update_xxx_translations.py` pour `locales/`) :

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "conversion_tools"))
from insert_index_html_keys import insert_keys

TRANSLATIONS = {
    "fr": {"ma_cle": "Valeur fr"},
    "en": {"ma_cle": "Value en"},
    # ... les 40 langues naturelles + tlh/qya/sjn (PAS les 3 variantes CSUR, générées à l'étape 2)
}
insert_keys(TRANSLATIONS, anchor_key="btn_dl2")
```

- `anchor_key` : clé JS existante après laquelle insérer les nouvelles clés dans chaque bloc de langue (choisir une clé présente dans tous les blocs, proche de l'endroit logique).
- Idempotent : si les clés existent déjà dans un bloc, elles sont retirées puis réinsérées (permet de relancer sans créer de doublon après correction d'une traduction).
- Ordre de travail identique à `locales/*.json` : fr d'abord, attendre validation utilisateur, puis le reste sur autorisation explicite.

### 2. `conversion_tools/convert_index_html_csur.py` — génère les 3 variantes CSUR

À lancer directement en CLI une fois que `tlh`, `qya`, `sjn` (versions latines) ont les nouvelles clés dans `index.html` :

```
python conversion_tools/convert_index_html_csur.py ma_cle_1 ma_cle_2 ...
```

- Réutilise `to_piqad()` / `split_preserve()` / `transcribe_batch()` des scripts `convert_piqad_csur.py` / `convert_tengwar_csur.py` **sans jamais toucher aux fichiers `locales/*.json`** (isole le code utile de ces scripts pour éviter d'exécuter leur conversion de fichier au niveau module).
- Écrit directement dans `index.html`, blocs `tlh-piqad`, `qya-tengwar`, `sjn-tengwar`, juste après `btn_dl2`.
- Idempotent (retire les valeurs existantes de ces clés avant de réinsérer).

### Pièges spécifiques à index.html

- **Espace insécable : `&nbsp;` (HTML) ≠ vrai caractère U+00A0 (JS)**. Le HTML statique interprète `&nbsp;` comme une entité ; le JS applique les valeurs via `el.textContent`, qui n'interprète **aucune** entité HTML — `&nbsp;` littéral s'affichera comme du texte brut. Dans une clé JS, toujours utiliser le vrai caractère Unicode U+00A0, jamais la séquence `&nbsp;`. Incident vécu (2026-07-07) : copié tel quel depuis le HTML source, affiché littéralement à l'écran.
  - **Et jamais un espace normal non plus** (autre incident, même jour, sens inverse) : en recopiant un texte HTML source du type `«&nbsp;Assets&nbsp;»` vers une clé de traduction, ne pas « désencoder » l'entité en simple espace ` ` — l'insécabilité serait perdue (retour à la ligne possible entre le guillemet et le mot). Relire le HTML source caractère par caractère avant d'écrire la clé (repérer `&nbsp;`, `&#8217;`, etc.) et restituer chaque entité sous la forme adaptée au système cible : caractère Unicode réel pour une clé JS/`textContent`, entité préservée telle quelle si la valeur est réinjectée en HTML.
- **Mots techniques/noms propres non couverts par `PRESERVE_RE`** : `locales/*.json` a accumulé au fil du temps une liste de tokens protégés (CBZ, MosaicView, ComicVine...) dans `convert_piqad_csur.py` / `convert_tengwar_csur.py`. Un mot nouveau utilisé uniquement dans `index.html` (ex. "Assets", "Releases" — sections de la page GitHub releases) n'y figure pas forcément : il sera alors partiellement transcrit en CSUR (lettres coïncidant avec des phonèmes klingon/tengwar converties). Avant de lancer `convert_index_html_csur.py`, vérifier que tout mot technique/nom propre de la nouvelle clé source (tlh/qya/sjn) est bien dans `PRESERVE_RE` des deux scripts ; sinon l'y ajouter d'abord (modification minime, cohérente avec les entrées existantes).
- **Qualité klingon/quenya/sindarin dans `index.html`** : même exigence que pour `locales/*.json` (pas de mots anglais/français bruts mélangés dans le texte construit) — vérifier avant de lancer le script d'insertion, pas après.
