---
name: verify-translation
description: Vérifie la qualité d'une traduction existante dans locales/*.json (contamination, exactitude vs le français de référence, cohérence terminologique), une langue à la fois selon une rotation persistée. À invoquer sur demande explicite ("vérifie une traduction", "/verify-translation").
---

# Vérification qualité des traductions — MosaicView

Audit de qualité d'une locale déjà traduite (pas un ajout de nouvelles clés — voir `add-translation` pour ça). Avec ~40 langues naturelles, ce skill n'en traite **qu'une seule par invocation** et retient l'historique dans `progress.json` (dans ce dossier de skill) pour reprendre à la langue suivante lors de la prochaine invocation.

## Langues exclues (ne jamais sélectionner, ne jamais proposer)

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

**Lire le fichier de la langue ET `fr.json` intégralement** (jamais un grep partiel isolé, jamais juste le bloc où on s'attend à trouver un problème — cf. piège vécu sur sjn où un audit par bloc local a manqué une divergence visible seulement à l'échelle du fichier entier).

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

## 4. Mise à jour de `progress.json`

Une fois l'audit terminé (que des corrections aient été appliquées ou non — une langue trouvée propre compte comme vérifiée), mettre à jour l'entrée de la langue dans `.claude/skills/verify-translation/progress.json` :
```json
"de": { "last_verified": "2026-07-14", "findings_count": 3 }
```
- `last_verified` : date du jour (voir contexte de conversation pour la date absolue, jamais une date relative).
- `findings_count` : nombre d'anomalies trouvées lors de cet audit (0 si rien trouvé).

Ne mettre à jour `progress.json` qu'à la toute fin, une fois l'audit (et les corrections éventuelles, si validées) effectivement terminés — pas en début de skill.
