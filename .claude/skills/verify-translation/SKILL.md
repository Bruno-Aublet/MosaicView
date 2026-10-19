---
name: verify-translation
description: Vérifie la qualité de traductions existantes dans locales/*.json (contamination, exactitude vs le français de référence, cohérence terminologique). Trois modes — "par langue" (défaut, un fichier entier à la fois selon une rotation persistée), "par clés" (une poignée de clés nommées, vérifiées dans toutes les langues) et "langue fictive" (audit complet de tlh.json, sjn.json ou qya.json avec leur lexique de référence). À invoquer sur demande explicite ("vérifie une traduction", "vérifie les clés X, Y", "audit complet du klingon", "/verify-translation"), et d'office en mode "par clés" après chaque ajout ou modification de traductions (skill add-translation), avec correction immédiate des anomalies.
---

# Vérification qualité des traductions — MosaicView

Audit de qualité de traductions déjà écrites (pas un ajout de nouvelles clés — voir `add-translation` pour ça). Trois modes :

| Mode | Périmètre | Quand | Sections |
|---|---|---|---|
| **Par langue** (défaut) | toutes les clés d'**une** langue | invocation sans clé nommée (`/verify-translation`, `/verify-translation de`, « vérifie une traduction ») | 1 à 4 |
| **Par clés** | **quelques** clés nommées, dans **toutes** les langues | l'utilisateur nomme des clés ou un bloc (`/verify-translation keys dialogs.scan.title buttons.retry`, « vérifie les clés que je viens d'ajouter ») | 5 |
| **Langue fictive** | toutes les clés de `tlh.json`, `sjn.json` **ou** `qya.json` | l'utilisateur nomme une langue fictive (`/verify-translation tlh`, « audit complet du sindarin ») | 6 |

Choix du mode : dès que la demande contient un ou plusieurs chemins de clé pointés (`section.sous.clé`), un préfixe de bloc, ou parle des « nouvelles clés / clés ajoutées », c'est le mode **par clés**. Un code de langue fictive (`tlh`, `sjn`, `qya`, ou une variante CSUR `tlh-piqad`/`sjn-tengwar`/`qya-tengwar`, ramenée à sa source latine) = mode **langue fictive**. Un autre code de langue seul (`de`, `ja`) ou aucune précision = mode **par langue**. En cas d'ambiguïté, demander.

## Mode « par langue »

Avec ~40 langues naturelles, ce mode n'en traite **qu'une seule par invocation** et retient l'historique dans `progress.json` (dans ce dossier de skill) pour reprendre à la langue suivante lors de la prochaine invocation.

## Langues exclues du mode « par langue » (ne jamais sélectionner, ne jamais proposer)

- `fr.json` — langue de référence, pas une traduction.
- `language_names.json` — structure spéciale (noms de langues traduits), pas les clés UI classiques.
- Les 6 fichiers des 3 langues fictives (2 variantes chacune) : `tlh.json`, `tlh-piqad.json`, `sjn.json`, `sjn-tengwar.json`, `qya.json`, `qya-tengwar.json`. Klingon/sindarin/quenya sont auditées par le mode **langue fictive** (section 6), uniquement sur demande nommée — jamais par la rotation. Ne jamais les inclure ici, même si `progress.json` ne les mentionne jamais (c'est normal et attendu).

Toutes les autres langues de `locales/*.json` sont éligibles.

## 1. Sélection de la langue à vérifier

1. Lister `locales/*.json` (ne jamais supposer la liste — le nombre de langues change). Retirer les exclusions ci-dessus.
2. Lire `.claude/skills/verify-translation/progress.json`. Format :
   ```json
   {
     "de": { "last_verified": "2026-07-14", "findings_count": 3 },
     "ja": { "last_verified": "2026-07-10", "findings_count": 0 }
   }
   ```
3. Choisir la langue par ordre alphabétique du code, selon cette priorité :
   - **En priorité** : la première langue (ordre alphabétique) absente de `progress.json` (jamais vérifiée).
   - **Si toutes les langues éligibles ont déjà une entrée** : celle dont `last_verified` est la date la plus ancienne. En cas d'égalité de date, la première par ordre alphabétique.
4. Annoncer la langue choisie et pourquoi (jamais vérifiée / dernière vérification le JJ/MM/AAAA) avant de commencer l'audit.

Ne jamais laisser l'utilisateur choisir la langue à sa place sauf s'il en nomme une explicitement dans sa demande (dans ce cas, vérifier qu'elle n'est pas dans la liste d'exclusion — langue fictive : passer au mode de la section 6 ; `fr`/`language_names` : le signaler et s'arrêter) — auquel cas traiter cette langue au lieu de suivre la rotation, mais quand même mettre à jour `progress.json` à la fin.

## 2. Méthode d'audit

**Lire le fichier de la langue ET `fr.json` intégralement** (jamais un grep partiel isolé, jamais juste le bloc où on s'attend à trouver un problème — un audit bloc par bloc manque les divergences visibles seulement à l'échelle du fichier entier, comme un même mot traduit différemment d'une section à l'autre).

Pour chaque section top-level (`buttons`, `dialogs`, `messages`, etc.), comparer clé par clé avec `fr.json` :

### a. Contamination par une autre langue
- Résidus de français resté en dur (copié-collé non traduit).
- Résidus d'anglais (souvent la langue pivot utilisée lors d'une traduction automatique ou manuelle bâclée) — sauf si la langue cible EST l'anglais.
- Mots d'une troisième langue naturelle sans rapport (signe d'un mauvais copier-coller entre scripts de traduction).
- Ne jamais confondre avec un emprunt légitime (mot technique international, nom propre MosaicView/ComicVine/ComicInfo/CBZ/CBR/PDF, terme intraduisible consciemment laissé tel quel) — si un doute existe sur un mot précis, le signaler comme incertain plutôt que de le corriger à l'aveugle.

### b. Exactitude de la traduction
- Sens contresens ou approximatif par rapport à `fr.json` (pas juste une reformulation naturelle — la langue cible doit dire la même chose).
- Paramètres `{param}`, `{count}`, `{path}`, etc. : présents à l'identique (même nom de placeholder) dans la traduction — un placeholder renommé ou perdu casse le `.format()`/`.format_map()` Python.
- Pluriels/accords cohérents avec la grammaire de la langue cible (pas un calque mot-à-mot du français qui produirait une grammaire incorrecte).
- Ponctuation adaptée aux conventions de la langue cible (espaces avant `:`/`!`/`?` en français uniquement, guillemets, etc. — ne pas imposer les conventions françaises à toutes les langues).

### c. Cohérence terminologique interne
- Un même concept technique (série, éditeur, résumé, bibliothèque, dossier, image, annuler/undo, refaire/redo, etc.) doit être traduit par le **même mot** partout dans le fichier, pas par plusieurs synonymes selon l'endroit.
- Construire une liste des concepts récurrents rencontrés et de leur traduction à chaque occurrence ; si un concept a plusieurs traductions différentes dans le fichier, c'est un finding à signaler (comparer les fréquences pour identifier laquelle est probablement la variante correcte, sans se fier au seul bloc local — même piège que sjn).

### d. Validité JSON et structure
- Le fichier doit rester un JSON valide (`json.load`) après toute correction.
- Aucune clé manquante ni clé en trop par rapport à `fr.json` (structure identique, valeurs différentes).

## 3. Rapport et corrections

1. Produire la liste des anomalies trouvées (fichier, chemin de clé, valeur actuelle, problème identifié, correction proposée).
2. **Ne pas corriger automatiquement sans validation** : présenter le rapport à l'utilisateur, attendre son accord avant d'éditer `locales/<code>.json`.
3. Corrections via l'outil Edit uniquement (jamais de script Bash/Python qui réécrit le fichier — règle du projet). Si le volume de corrections est important, un script Python de lecture/écriture JSON reste possible en dernier recours seulement si l'utilisateur le préfère explicitement (comme pour `add-translation`), sinon Edit direct.
4. Après correction, revalider que le JSON se parse (`python -c "import json; json.load(open('locales/xx.json', encoding='utf-8'))"`).

### Cocher les cases du fichier de suivi `audit_traduction_<code>.md`

Quand un groupe est entièrement corrigé, cocher toutes ses cases d'un coup avec **une seule commande `sed` limitée au bloc du groupe** — jamais avec Edit (il faudrait renvoyer tout le bloc deux fois, avant et après : très coûteux en tokens sur les gros groupes), jamais une édition par case. C'est la seule exception à la règle « pas de script shell pour réécrire un fichier » de `CLAUDE.md`.

Le bloc d'un groupe va de son titre `## X —` jusqu'au titre du groupe suivant ; pour le dernier groupe (E), jusqu'à la fin du fichier (`$`) :
```bash
sed -i '/^## A /,/^## B /s/^- \[ \]/- [x]/' audit_traduction_<code>.md
sed -i '/^## B /,/^## C /s/^- \[ \]/- [x]/' audit_traduction_<code>.md
sed -i '/^## C /,/^## D /s/^- \[ \]/- [x]/' audit_traduction_<code>.md
sed -i '/^## D /,/^## E /s/^- \[ \]/- [x]/' audit_traduction_<code>.md
sed -i '/^## E /,$s/^- \[ \]/- [x]/' audit_traduction_<code>.md
```
- Si un groupe est absent du fichier (aucune anomalie de ce type), borner la plage sur le titre du groupe suivant réellement présent.
- Vérifier ensuite le nombre de cases restantes dans le groupe : `awk '/^## B /,/^## C /' audit_traduction_<code>.md | grep -c '^- \[ \]'` (doit afficher 0).
- La commande ne s'applique qu'aux fichiers `audit_traduction_*.md` : les corrections de `locales/<code>.json` passent toujours par Edit.

## 4. Mise à jour de `progress.json`

Une fois l'audit terminé (que des corrections aient été appliquées ou non — une langue trouvée propre compte comme vérifiée), mettre à jour l'entrée de la langue dans `.claude/skills/verify-translation/progress.json` :
```json
"de": { "last_verified": "2026-07-14", "findings_count": 3 }
```
- `last_verified` : date du jour (voir contexte de conversation pour la date absolue, jamais une date relative).
- `findings_count` : nombre d'anomalies trouvées lors de cet audit (0 si rien trouvé).

Ne mettre à jour `progress.json` qu'à la toute fin, une fois l'audit (et les corrections éventuelles, si validées) effectivement terminés — pas en début de skill.

## 5. Mode « par clés » — quelques clés, toutes les langues

Usage typique : 2 ou 3 clés venant d'être ajoutées (via `add-translation` ou à la main), dont on veut contrôler la traduction partout sans auditer 40 fichiers entiers. **Lancé d'office à la fin de chaque ajout ou modification de traductions** (skill `add-translation`, « Vérification finale », étape 5) : dans ce cas, les anomalies sont corrigées aussitôt, sans attendre l'accord de l'utilisateur (voir 5.6). Les règles d'audit sont celles de la section 2 (a à d), appliquées « en travers » : clé par clé, langue par langue.

### 5.1 Résolution des clés

1. Clés nommées explicitement : les prendre telles quelles. Préfixe de bloc (`dialogs.scan`) : toutes les clés feuilles sous ce préfixe dans `fr.json`.
2. « Les clés que je viens d'ajouter » sans liste : les déduire de `git diff -U0 locales/fr.json` (lignes `+` uniquement, chemins reconstitués depuis la structure de `fr.json`), puis **présenter la liste à l'utilisateur et attendre sa confirmation** avant de lancer la vérification.
3. Chaque clé doit exister dans `fr.json` ; sinon le signaler et s'arrêter pour cette clé (faute de frappe probable).
4. Au-delà d'une vingtaine de clés, proposer plutôt le mode par langue sur les langues concernées : le mode par clés n'est pas fait pour ça.

### 5.2 Contexte (avant de juger une traduction)

- Lire dans `fr.json` le bloc parent complet de chaque clé (les clés sœurs) : le sens d'un libellé court (« Appliquer », « Source ») dépend de son voisinage.
- Repérer l'usage dans le code (Grep sur le chemin de clé dans `modules/` et `MosaicView.py`) : libellé de bouton/menu (pas de `...` final, règle 8 de `add-translation`), titre de fenêtre (lu via `_wt()`, concerne les variantes CSUR), message, tooltip HTML, texte paramétré.
- **Termes de référence** : pour chaque concept récurrent présent dans le texte français (série, page, image, archive, bibliothèque, dossier, annuler, etc.), choisir 2 ou 3 clés **existantes** de `fr.json` qui emploient ce même concept ; elles serviront à vérifier la cohérence terminologique (point c de la section 2) sans relire chaque fichier entier.

### 5.3 Langues couvertes

Toutes les `locales/*.json` sauf `fr.json` (référence) et `language_names.json`. **Contrairement au mode par langue, les langues fictives sont incluses**, car ce sont justement les clés neuves qui y introduisent des erreurs :
- `tlh`, `sjn`, `qya` : appliquer la section « Vérification qualité klingon/elfique/gallois » du skill `add-translation` (lexiques obligatoires : `conversion_tools/klingon/lexique_tlh_canon.md` pour tlh, `conversion_tools/sindarin/lexique_sjn_canon.md` pour sjn, `conversion_tools/quenya/lexique_qya_canon.md` pour qya ; + scan des résidus anglais ; + contrôle lexical `conversion_tools/klingon/check_tlh.py` pour tlh, `conversion_tools/sindarin/check_sjn.py` pour sjn, `conversion_tools/quenya/check_qya.py` pour qya).
- `tlh-piqad`, `sjn-tengwar`, `qya-tengwar` : ne pas juger la langue (c'est une transcription) ; vérifier seulement que la valeur existe, que les placeholders sont intacts, qu'elle est bien transcrite (contient des caractères PUA) sauf pour les clés exclues de la conversion (`app_title`, `app_baseline`, `window_title`, `quality_window_title`, `icons_window_title`, qui restent en latin), et qu'elle ne contient pas de `"`. Une valeur CSUR qui ne correspond pas à sa source latine = conversion non relancée après modification de `tlh`/`sjn`/`qya`.

### 5.4 Extraction (lecture seule)

Un script Python de **lecture** (aucune écriture dans `locales/`) extrait, pour chaque clé vérifiée et chaque terme de référence, la valeur dans chaque locale, et écrit le résultat en UTF-8 dans un fichier du scratchpad, relu ensuite avec Read (le terminal cp1252 ne sait pas afficher l'arménien, le thaï, les PUA, etc. — jamais de `print()` direct). Le même script fait les contrôles mécaniques, signalés comme « suspects » à confirmer à la relecture :
- clé absente ;
- ensemble des placeholders `{...}` différent de celui de `fr.json` ;
- valeur identique au français (hors `fr`) ou identique à l'anglais (hors `en`) — faux positifs légitimes possibles : noms propres, sigles, formats (`CBZ`, `ZIP`, `OK`) ;
- `...` / `…` final sur un libellé de bouton ou de menu ;
- `zh-CN` / `zh-TW` : deux-points pleine chasse `：` ;
- `hy` : caractères hors du bloc arménien (même test que la section « Traduction arménien » de `add-translation`) ;
- variantes CSUR : contrôles de 5.3.

### 5.5 Relecture

Pour chaque clé, passer toutes les langues une par une et appliquer les points a, b, c de la section 2 : contamination, exactitude par rapport au français (dans le contexte établi en 5.2), placeholders, grammaire, ponctuation propre à la langue, et **même mot que dans les termes de référence** de cette langue pour chaque concept récurrent. Si les termes de référence eux-mêmes divergent entre eux dans une langue, le signaler comme incohérence préexistante (hors périmètre : la noter, ne pas la corriger sans accord) plutôt que de choisir au hasard.

### 5.6 Rapport et corrections

1. Rapport groupé par clé : pour chaque anomalie, langue, valeur actuelle, problème, correction proposée ; puis la liste des langues trouvées propres pour cette clé (un simple « 38 langues OK : ar, bg, … » suffit).
2. **Pas de correction sans accord** de l'utilisateur, comme en section 3 — **sauf quand la vérification suit un ajout ou une modification de traductions** (skill `add-translation`, « Vérification finale », étape 5) : les anomalies sont alors corrigées aussitôt (consigne de l'utilisateur), et le rapport indique ce qui a été trouvé et corrigé. Corrections via Edit uniquement, sauf préférence explicite de l'utilisateur pour un script de réécriture JSON (les langues fictives passent par leur `run_block.sh`).
3. Variantes CSUR : ne jamais les éditer directement. Corriger la source latine (`tlh`/`sjn`/`qya`), puis relancer le script de conversion correspondant (voir tableau « Langues fictives » de `add-translation`).
4. Revalider avec `json.load` chaque fichier modifié.
5. **Ne pas toucher à `progress.json`** (il ne trace que les audits complets du mode par langue — une vérification de 3 clés ne rend pas une langue « vérifiée ») et ne pas créer de fichier `audit_traduction_<code>.md` : le rapport tient dans la réponse.
6. Hors périmètre : les traductions du bloc JS d'`index.html` (si les clés y ont aussi été ajoutées, le signaler à l'utilisateur sans les vérifier, sauf demande). Exception : le mode « langue fictive » (section 6) inclut toujours le bloc d'`index.html` de la langue auditée.

## 6. Mode « langue fictive » — audit complet de tlh, sjn ou qya

Les trois fichiers latins accumulent des erreurs dispersées (mots hors lexique, mots d'une autre langue fictive, contresens, plusieurs mots pour un même concept) : une correction ponctuelle bloc par bloc en fait remonter de nouvelles à chaque passage. Ce mode traite **un fichier entier**, livre **toutes** ses anomalies en une seule fois, puis corrige ce que l'utilisateur retient.

Les règles de fond (lexiques, orthographe klingonne, résidus anglais, pièges `tengwa` / mot déjà pris pour un autre concept) sont celles de la section « Vérification qualité klingon/elfique/gallois » du skill `add-translation` : ce mode en est l'application à l'échelle d'un fichier complet, il ne les duplique pas.

### 6.1 Périmètre

- **Une seule langue par invocation**, nommée par l'utilisateur (pas de rotation, pas d'entrée dans `progress.json` — le suivi est le fichier `audit_traduction_<code>.md`).
- Seuls `tlh.json`, `sjn.json`, `qya.json` et le bloc latin correspondant d'`index.html` sont audités. Les variantes CSUR (`tlh-piqad`, `sjn-tengwar`, `qya-tengwar`) ne sont jamais jugées ni éditées : elles sont régénérées à la fin (6.6).
- **Le bloc JS d'`index.html` (page web) fait partie de l'audit** : le bloc de la langue auditée (`"tlh"`, `"sjn"` ou `"qya"` dans `const translations`) est relu avec les mêmes règles et le même lexique que le fichier `locales/`, et ses anomalies figurent dans le même rapport sous le préfixe `index.html:` (ex. `index.html:feat_dual_desc`). Ses clés n'ont rien de commun avec `locales/*.json` : un audit limité à `locales/` laisse la page web dans son ancien vocabulaire. Ses valeurs (y compris chaque nom de `lang_names`) passent par le contrôle lexical de la langue (`check_tlh.py` / `check_sjn.py` / `check_qya.py`). Son bloc CSUR (`"tlh-piqad"`, `"sjn-tengwar"`, `"qya-tengwar"`) n'est jamais jugé ni édité à la main : il est régénéré (6.6).

### 6.2 Préalables

1. Lire **en entier** le lexique de la langue (tlh : `conversion_tools/klingon/lexique_tlh_canon.md` ; sjn : `conversion_tools/sindarin/lexique_sjn_canon.md` ; qya : `conversion_tools/quenya/lexique_qya_canon.md`) et la section « Vérification qualité klingon/elfique/gallois » d'`add-translation`.
   - **Vérifier le lexique lui-même contre une source canonique, pas seulement le fichier contre le lexique.** Un lexique bâti sur « l'usage dominant » du fichier audité recopie ses erreurs (pièges vérifiés : en klingon `De'wI'` signifie « ordinateur », pas « fichier » ; en sindarin `Aew` signifie « petit oiseau », pas « erreur », et `randir` « vagabond », pas « image »). Pour tlh, l'index du dictionnaire boQwI' (`conversion_tools/klingon/boqwi_index.tsv`, consulté avec `lookup.py`) sert de référence ; pour sjn, l'index Eldamo (`conversion_tools/sindarin/eldamo_sjn_index.tsv`, consulté avec `conversion_tools/sindarin/lookup.py`). Pour qya, l'index Eldamo quenya (`conversion_tools/quenya/eldamo_qya_index.tsv`, consulté avec `conversion_tools/quenya/lookup.py`) ; l'ancien lexique quenya, bâti sur l'usage du fichier, était faux (`fána` = blanc pour « image », `Russë` = chevelure rousse pour « erreur »).
   - **Les choix linguistiques ne se soumettent pas à l'utilisateur** : il n'a pas la compétence pour valider un mot elfique ou klingon. Claude applique lui-même la règle de choix du lexique (mot attesté > néologisme recensé avec son auteur > mot construit uniquement d'éléments attestés), vérifie qu'il n'entre pas en collision avec un autre concept, et tranche. Seules les questions pratiques (lancer une retraduction, toucher à la mémoire ou à `idees.txt`) se posent à l'utilisateur.
2. Si un fichier `audit_traduction_<code>.md` existe déjà à la racine : c'est un audit en cours. Reprendre les cases non cochées au lieu de relancer un audit, sauf demande contraire.

### 6.3 Pré-scan mécanique (lecture seule)

Un script Python de **lecture** (aucune écriture dans `locales/`), écrit dans le scratchpad, sortie UTF-8 dans un fichier du scratchpad relu avec Read (jamais de `print()` direct, terminal cp1252). Il sert à orienter la relecture, jamais à la remplacer :
- structure : clés manquantes/en trop par rapport à `fr.json`, placeholders `{...}` divergents ;
- valeurs identiques au français (faux positifs : noms propres, sigles, formats) ;
- tlh : `conversion_tools/klingon/check_tlh.py` (chaque mot doit se décomposer en racine et suffixes canoniques, noms propres et sigles déclarés dans sa liste `ALLOWED`) ;
- sjn : `conversion_tools/sindarin/check_sjn.py` (chaque mot doit être un mot Eldamo après avoir défait mutation initiale et terminaison verbale, ou une forme déclarée dans `vocab_sjn.tsv` ; les faux sens connus sont refusés) ;
- qya : `conversion_tools/quenya/check_qya.py` (chaque mot doit être un mot Eldamo après avoir retiré jusqu'à trois terminaisons quenya empilées, ou une forme déclarée dans `vocab_qya.tsv` ; les faux sens connus et les mots anglais/français courants sont refusés) ;
- résidus anglais (regex d'`add-translation`, avec ses faux positifs `and` sindarin / `not` klingon) ;
- mots d'une autre langue fictive : chaque mot du fichier présent dans les deux autres fichiers fictifs mais absent du lexique de la langue auditée est un suspect (ex. `tengwa` hors qya) ;
- **inventaire terminologique** : pour chaque concept du lexique, et pour chaque concept récurrent de `fr.json` (série, page, fichier, image, dossier, métadonnées, erreur, coller, copier, adresse, introuvable…), la liste des traductions rencontrées avec leur nombre d'occurrences et les clés concernées.

### 6.4 Relecture complète

Lire `fr.json` et le fichier audité **intégralement**, section top-level par section top-level, et comparer chaque clé au français (points a, b, c de la section 2). En plus du pré-scan, la relecture seule repère :
- un mot valide de la langue employé à contresens (`lulegh` « ils le voient » pour « copier ») ;
- une construction qui ne dit pas la même chose que le français (négation, sujet, impératif vs constat) ;
- un mot du lexique remplacé par un synonyme local.

Ne jamais trancher un concept que le lexique déclare **non tranché** sans chercher d'abord dans la source canonique (en klingon, « maintenir une touche » = `'uch` et « ordre » = `patlh` y figurent) : à défaut, construire un mot selon la règle de choix (6.2) et le trancher soi-même, sans le soumettre à l'utilisateur. Avant de proposer un mot comme correction, vérifier ses autres usages dans le fichier (piège « mot déjà pris pour un autre concept » d'`add-translation`).

### 6.5 Rapport : fichier `audit_traduction_<code>.md`

Livré **complet, en une seule fois**, avant toute correction. Fichier à la racine du dépôt :

```markdown
# Audit <code>.json — AAAA-MM-JJ

Total : N anomalies (A : n, B : n, C : n, D : n, E : n)

## A — Mots étrangers
- [ ] `chemin.de.clé` — `valeur actuelle` — problème — correction proposée : `…`

## B — Contresens
## C — Écarts au lexique / plusieurs mots pour un concept
## D — Mots inventés ou douteux
## E — Divers
```

- **A — Mots étrangers** : anglais, français, gallois, mot d'une autre langue fictive.
- **B — Contresens** : mot valide mais sens faux, construction qui dit autre chose que le français.
- **C — Écarts au lexique / plusieurs mots pour un concept** : mot qui contredit le lexique, ou concept traduit de plusieurs façons dans le fichier (une entrée par concept, avec les variantes, leurs occurrences et la liste des clés à aligner). Les concepts non tranchés y figurent avec le mot retenu par Claude (6.4) et sa justification.
- **D — Mots inventés ou douteux** : forme introuvable dans le lexique ou incertaine ; signaler l'incertitude plutôt que d'affirmer une correction.
- **E — Divers** : ponctuation, `...` final sur un bouton/menu, guillemet `"` dans une phrase à transcrire (retiré en CSUR), espaces, structure.
- Un groupe sans anomalie est omis (les plages `sed` de la section 3 se bornent alors au groupe suivant présent).
- Dans la réponse : les totaux par groupe, les mots fixés par Claude pour les concepts non tranchés, et la question pratique de la suite (corriger case par case ou retraduire) — pas la recopie du fichier.

### 6.6 Corrections

1. **Aucune correction sans accord** : l'utilisateur choisit les groupes à corriger, ou la retraduction complète. Quand l'audit montre d'emblée une densité d'erreurs massive (cas du sindarin, où les mots faux du lexique touchaient à eux seuls 418 valeurs sur 1 721), proposer directement la retraduction complète sans dresser le rapport case par case, qui coûte cher pour rien.
2. Corrections ponctuelles de `<code>.json` via Edit. Cocher un groupe entièrement corrigé avec la commande `sed` de la section 3 ; une entrée isolée volontairement écartée par l'utilisateur est retirée du fichier ou annotée selon sa consigne.
   - **Densité d'anomalies trop forte** (une large part du fichier fautive) : proposer une retraduction complète plutôt qu'une correction case par case. Elle se fait par blocs (une section top-level ou un groupe de sous-sections à la fois), en respectant le lexique ; un script d'application n'est utilisé qu'avec l'accord explicite de l'utilisateur. Pour tlh : `bash conversion_tools/klingon/run_block.sh <bloc.json> <prefixe>...` (contrôles lexical et structurel, sauvegarde, application, régénération pIqaD ; rien n'est écrit si un contrôle échoue). Pour sjn : `bash conversion_tools/sindarin/run_block.sh <bloc.json> <prefixe>...` (même chaîne, régénération tengwar ; pour un bloc de clés isolées, passer chaque clé comme préfixe). Pour qya : `bash conversion_tools/quenya/run_block.sh <bloc.json> <prefixe>...` (même chaîne). Ces scripts ne touchent que `locales/` : le bloc d'`index.html` se retraduit à part, valeurs contrôlées par `check_tlh.py` / `check_sjn.py` / `check_qya.py` avant écriture (format `{"clé": "valeur"}`, `lang_names` aplati en une seule chaîne), puis bloc CSUR régénéré (point 3). Les champs répétés dans plusieurs blocs (ComicInfo, colonnes de la bibliothèque, `metadata`) et les dialogues construits sur un même modèle (traitements par lot) se génèrent à partir d'un dictionnaire unique, pour garantir un mot par concept.
3. Après chaque lot de corrections :
   - `json.load` du fichier modifié ;
   - nouveau passage du scan résidus anglais (et `check_tlh.py` pour tlh, `check_sjn.py` pour sjn, `check_qya.py` pour qya) sur les valeurs modifiées ;
   - régénération de la variante CSUR depuis la source latine (`convert_piqad_csur.py` ou `convert_tengwar_csur.py`, voir tableau « Langues fictives » d'`add-translation`), puis `json.load` de la variante ;
   - si le bloc d'`index.html` a été corrigé : régénération de son bloc CSUR, puis validation de l'objet `const translations` entier (`json.loads`) et contrôle que les blocs latin, CSUR et `fr` ont les mêmes clés et le même nombre d'entrées dans `lang_names`. `convert_index_html_csur.py` ne traite que des clés texte, les trois langues fictives à la fois, et les réinsère après `btn_dl2` : il ne convient pas pour régénérer `lang_names` ni un seul bloc. Dans ce cas, un script ponctuel (accord de l'utilisateur, sauvegarde d'`index.html` dans le scratchpad, comparaison affichée) remplace les valeurs du bloc CSUR en place, dans son ordre de clés, avec `to_piqad()` de `convert_piqad_csur.py` (pour tengwar : `split_preserve()`/`transcribe_batch()` de `convert_tengwar_csur.py`). Le `badge` du bloc CSUR garde sa valeur.
4. Un mot retenu pour un concept absent du lexique : l'ajouter au lexique (tlh : `conversion_tools/klingon/lexique_tlh_canon.md` ; sjn : `conversion_tools/sindarin/lexique_sjn_canon.md`, et sa forme construite dans `vocab_sjn.tsv` ; qya : `conversion_tools/quenya/lexique_qya_canon.md`, et sa forme construite dans `vocab_qya.tsv`) et le signaler dans la réponse.
5. Quand toutes les cases sont cochées, demander à l'utilisateur s'il garde ou supprime `audit_traduction_<code>.md`.
