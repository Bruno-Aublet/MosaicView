---
name: verify-translation
description: Vérifie la qualité de traductions existantes dans locales/*.json (contamination, exactitude vs le français de référence, cohérence terminologique). Deux modes — "par langue" (défaut, un fichier entier à la fois selon une rotation persistée) et "par clés" (une poignée de clés nommées, vérifiées dans toutes les langues). À invoquer sur demande explicite ("vérifie une traduction", "vérifie les clés X, Y", "/verify-translation").
---

# Vérification qualité des traductions — MosaicView

Audit de qualité de traductions déjà écrites (pas un ajout de nouvelles clés — voir `add-translation` pour ça). Deux modes :

| Mode | Périmètre | Quand | Sections |
|---|---|---|---|
| **Par langue** (défaut) | toutes les clés d'**une** langue | invocation sans clé nommée (`/verify-translation`, `/verify-translation de`, « vérifie une traduction ») | 1 à 4 |
| **Par clés** | **quelques** clés nommées, dans **toutes** les langues | l'utilisateur nomme des clés ou un bloc (`/verify-translation keys dialogs.scan.title buttons.retry`, « vérifie les clés que je viens d'ajouter ») | 5 |

Choix du mode : dès que la demande contient un ou plusieurs chemins de clé pointés (`section.sous.clé`), un préfixe de bloc, ou parle des « nouvelles clés / clés ajoutées », c'est le mode **par clés**. Un code de langue seul (`de`, `ja`) ou aucune précision = mode **par langue**. En cas d'ambiguïté, demander.

## Mode « par langue »

Avec ~40 langues naturelles, ce mode n'en traite **qu'une seule par invocation** et retient l'historique dans `progress.json` (dans ce dossier de skill) pour reprendre à la langue suivante lors de la prochaine invocation.

## Langues exclues du mode « par langue » (ne jamais sélectionner, ne jamais proposer)

- `fr.json` — langue de référence, pas une traduction.
- `language_names.json` — structure spéciale (noms de langues traduits), pas les clés UI classiques.
- Les 6 fichiers des 3 langues fictives (2 variantes chacune) : `tlh.json`, `tlh-piqad.json`, `sjn.json`, `sjn-tengwar.json`, `qya.json`, `qya-tengwar.json`. Klingon/sindarin/quenya sont vérifiées par un mécanisme dédié (lexiques de référence en mémoire `reference_tlh_klingon_glossary`, `reference_sjn_sindarin_glossary`, `reference_qya_quenya_glossary` + section "Vérification qualité klingon/elfique/gallois" du skill `add-translation`). Ne jamais les inclure ici, même si `progress.json` ne les mentionne jamais (c'est normal et attendu).

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

Ne jamais laisser l'utilisateur choisir la langue à sa place sauf s'il en nomme une explicitement dans sa demande (dans ce cas, vérifier qu'elle n'est pas dans la liste d'exclusion, sinon le signaler et s'arrêter) — auquel cas traiter cette langue au lieu de suivre la rotation, mais quand même mettre à jour `progress.json` à la fin.

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

Usage typique : 2 ou 3 clés venant d'être ajoutées (via `add-translation` ou à la main), dont on veut contrôler la traduction partout sans auditer 40 fichiers entiers. Les règles d'audit sont celles de la section 2 (a à d), appliquées « en travers » : clé par clé, langue par langue.

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
- `tlh`, `sjn`, `qya` : appliquer la section « Vérification qualité klingon/elfique/gallois » du skill `add-translation` (lexiques mémoire `reference_tlh_klingon_glossary`, `reference_sjn_sindarin_glossary`, `reference_qya_quenya_glossary` obligatoires + scan des résidus anglais).
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
2. **Pas de correction sans accord** de l'utilisateur, comme en section 3. Corrections via Edit uniquement, sauf préférence explicite de l'utilisateur pour un script de réécriture JSON.
3. Variantes CSUR : ne jamais les éditer directement. Corriger la source latine (`tlh`/`sjn`/`qya`), puis relancer le script de conversion correspondant (voir tableau « Langues fictives » de `add-translation`).
4. Revalider avec `json.load` chaque fichier modifié.
5. **Ne pas toucher à `progress.json`** (il ne trace que les audits complets du mode par langue — une vérification de 3 clés ne rend pas une langue « vérifiée ») et ne pas créer de fichier `audit_traduction_<code>.md` : le rapport tient dans la réponse.
6. Hors périmètre : les traductions du bloc JS d'`index.html` (si les clés y ont aussi été ajoutées, le signaler à l'utilisateur sans les vérifier, sauf demande).
