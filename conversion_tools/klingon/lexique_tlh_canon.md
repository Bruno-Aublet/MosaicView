# Lexique klingon de référence (`locales/tlh.json` et bloc `"tlh"` d'`index.html`)

S'applique aussi à la page web : le bloc `"tlh"` de `const translations` dans `index.html` emploie les mêmes mots que l'application.

Source de vérification : le dictionnaire libre boQwI' ([klingon-assistant-data](https://github.com/De7vID/klingon-assistant-data)), qui recense les mots canoniques (Marc Okrand : TKD, KGT, qep'a', Saarbrücken qepHom'a'…) avec leur source, soit environ 6 400 entrées.

Règle : le mot canonique quand il existe, même plus long ; sinon une construction faite uniquement de racines et de suffixes canoniques. Jamais un mot retenu parce qu'il est fréquent dans `tlh.json`.

Légende de la colonne « Statut » :
- **canon** : mot attesté tel quel, source entre parenthèses ;
- **construit** : assemblage de mots et de suffixes tous attestés (ex. `-wI'` « celui / ce qui fait », `-ghach` nominalisation, `-moH` causatif, `-meH` « pour », `-Hom` diminutif), aucun n'est inventé.

Colonne « À ne pas employer » : mots faux ou inexistants pour ce concept, avec leur sens réel quand ils existent.

## 0. Outils de ce dossier (`conversion_tools/klingon/`)

| Fichier | Rôle |
|---|---|
| `boqwi_index.tsv` | Index du dictionnaire boQwI' (mot, nature, définition anglaise et française, source). Régénérable avec `build_index.py <dossier des mem-*.xml> boqwi_index.tsv` après téléchargement des XML depuis klingon-assistant-data. |
| `lookup.py` | Recherche dans l'index : `python conversion_tools/klingon/lookup.py conversion_tools/klingon/boqwi_index.tsv sortie.txt print "k:qIl"` — un terme anglais cherche dans les définitions, `k:mot` cherche un mot klingon exact. Sortie UTF-8 dans un fichier (le terminal cp1252 n'affiche pas tout). |
| `check_tlh.py` | Contrôle lexical : chaque mot doit se décomposer en préfixe + racine canonique + suffixes canoniques, ou figurer dans la liste `ALLOWED` (noms propres, sigles, formats). Repère les mots inventés et les fautes de frappe, pas les contresens. |
| `check_struct.py` | Contrôle structurel d'un bloc par rapport à `fr.json` : clés, `{placeholders}`, retours à la ligne, guillemets droits. |
| `apply_block.py` | Applique un bloc `{"chemin.de.clé": valeur}` à `locales/tlh.json` en conservant ordre, indentation et fins de ligne CRLF. |
| `run_block.sh` | Chaîne complète : contrôles, sauvegarde dans `%TEMP%\mosaicview_tlh_backup`, application, régénération pIqaD, validation. |

## 1. Faux sens vérifiés

| Mot | Sens réel (canon) | Concept pour lequel il ne convient pas |
|---|---|---|
| `De'wI'` | **ordinateur** (TKD) | fichier |
| `cha'nob` | **cadeaux rituels** (KGT) | image |
| `Qaw'` | **détruire** (TKD) | Annuler (bouton) — le canon est `qIl` « annuler, interrompre (un logiciel) » (TKD) |
| `ngor` | **tricher** (TKD) | convertir |
| `tetlh` | **liste, rouleau** (KGT) | série |
| `chabal tetlh` | **liste d'envies** (2015) | dossier |
| `nIqHom` | **logiciel** (qep'a' 23) | bibliothèque |
| `ghItlh` | **manuscrit / écrire** (TKD) | page — le canon est `tenwal` |
| `mung qaw` | « mémoire de l'origine » (`mung` = origine) | métadonnées |
| `chelmoH` | « faire ajouter » | copier |
| `HevleH`, `Qolqo'`, `Hur'`, `vI'ang` | n'existent pas dans le canon | éditeur, police, mosaïque, refaire |
| `gho` | **cercle** (TKD) | molette — le canon est `rutlhHom` |
| `pat` | **système** (TKD) | page web |
| `nIt` | **être pur** | droit / redresser — le canon est `wan` « être droit » |
| `qul De'wI'` | « ordinateur de feu » | ordinateur — `De'wI'` seul suffit |

Grammaire : `joq` relie des noms (`JPG, WEBP joq AVIF`), `pagh` relie des propositions ; `-lu'` et `-laH` sont tous deux de type 5 et ne se combinent jamais.

## 2. Lexique

### Fichiers, archives, stockage
| Concept | Mot | Statut | À ne pas employer |
|---|---|---|---|
| fichier | `'a'baqa'` / `'a'baqa'mey` | canon (qep'a' 31, 2024 : « fichier, dossier, portfolio ») | `De'wI'` (= ordinateur) |
| dossier, répertoire | `'a'baqa' ngaSwI'` (« conteneur de fichiers ») | construit (`ngaSwI'` canon « conteneur ») | `chabal tetlh` (= liste d'envies) |
| sous-dossier | `'a'baqa' ngaSwI'Hom` | construit | |
| racine | `'a'baqa' ngaSwI' wa'DIch` | construit | `jer` |
| répertoire maître (bibliothèque) | `'a'baqa' ngaSwI' nIv` (`nIv` « être supérieur ») | construit | `De' qoj` |
| archive (CBZ, CBR…) | `vey` | canon (2021 : « paquet, ensemble ») | `per` (= étiquette) |
| extension | `mojaq` | canon (TKD : « suffixe ») | `mIv` (= casque) |
| format | `tu'qom` | canon (2016 : « forme, configuration ») | `cham` (= technologie) |
| ordinateur | `De'wI'` | canon (TKD) | `qul De'wI'` |
| données | `De'` | canon (TKD) | |
| base de données | `qawHaq` | canon (TKD : « banques de données ») | `De'wI' Soj` (= nourriture d'ordinateur) |
| fichiers temporaires | `'a'baqa'mey ru'` | construit (`ru'` canon « être temporaire ») | |
| registre Windows | `Windows ta` | canon (`ta` TKD : « registre, archive ») | |
| corbeille | `veQ ngaSwI'` | construit (`veQ` canon « ordures ») | `woDmeH ngaSwI'` |
| presse-papiers | `velqa' ngaSwI'` | construit (aligné sur « copier ») | `chelmoHmeH ngaSwI'` |
| journal (log) | `QonoS` | canon (TKD : « journal, registre ») | `log`, `mIllogh` |
| journal d'erreurs ; erreur inattendue | `Qagh QonoS` ; `pIHbe'lu'bogh Qagh` (« erreur qu'on n'attend pas ») | construits sur canon (`pIH` « attendre ») | |
| dernières actions (journal) ; fin d'une opération | `Qu'mey Qav` ; `Qu' rIn` | canon (`Qav` « être dernier », `rIn` « être achevé ») | |
| tâche (traitement en cours) ; se dérouler | `Qu'` ; `ruch` (`ruchtaHvIS` « pendant qu'elles se déroulent ») | canon (`Qu'` « tâche, mission », `ruch` « procéder ») | |
| même ; précédent ; N fois | `rap` ; `vorgh` ; `{count}logh` | canon (`rap` « être le même », `vorgh` « être précédent », suffixe `-logh`) | |
| erreur rattrapée / non rattrapée ; ignorée par Python ; moteur PDF | `Qagh SeHlu'bogh` / `Qagh SeHlu'be'bogh` ; `Qagh buSHa'bogh Python` ; `PDF jo'Hom` | construits sur canon (`SeH` « contrôler », `buSHa'` « ignorer », `jo'` « machine » + `-Hom`) | |
| démarrer (l'application) ; dernier ; problème ; conserver | `tagh` ; `Qav` ; `qay'` (verbe « être un problème ») ; `pol` | canon | |
| il y a N jours | `jaj N ret` | canon (`ret` « période de temps passée ») | |
| mot de passe | `chaw' ngoq` | canon (2014) | `mI'` |
| déverrouiller / verrouiller | `ngaQHa'moH` / `ngaQmoH` | canon | `poSmoH` |
| chiffrer | `So'` | canon (2014) | |
| compte (service) | `mab` | canon (2018) | `pong` |

### BD et métadonnées
| Concept | Mot | Statut | À ne pas employer |
|---|---|---|---|
| BD (album, fichier de BD) | `mIllogh paq` (« livre d'images ») | construit — `mIllogh mIr` (canon) désigne le strip ou le dessin animé | `paq` seul |
| bande dessinée (genre) | `mIllogh mIr` | canon (qep'a' 29 : « bande dessinée, cartoon ») | |
| page | `tenwal` / `tenwalmey` | canon (2015 : « page d'un livre ») | `ghItlh` (= manuscrit) |
| image | `mIllogh` / `mIlloghmey` | canon (2010 : « image, représentation visuelle ») | `cha'nob` (= cadeaux rituels) |
| vignette | `mIllogh mach` | construit | `mach cha'nob` |
| série | `lut mIr` | canon (2017 : « série d'histoires, de films ») | `tetlh` (= liste) |
| numéro (d'une série) | `mI'` | canon | `De' qun`, `mIS`, `mIw` |
| volume | `paq` | canon (« livre ») | |
| métadonnées | `paq De'` (« informations du livre ») | construit, aucun mot canon | `mung qaw` |
| résumé | `jo'Ha'ghach` (`jo'Ha'` « résumer, dézoomer ») | construit sur un canon de qep'a' 33 | `ja'ghach mach` |
| éditeur (maison) | `paq chenmoHwI'` | construit | `HevleH` (inexistant) |
| éditeur (personne) | `DI'wI'` (`DI'` « éditer, réviser ») | construit sur un canon de qep'a' 28 | `choHwI'` |
| scénariste | `lut ghItlhwI'` | construit | `qonwI'` / `ghItlhwI'` |
| dessinateur | `DIjwI'` | canon (2018 : « artiste, peintre ») | |
| encreur | `rItlh laSwI'` (`laS` « encrer ») | construit | |
| coloriste | `qalmuS chenmoHwI'` | construit | |
| lettreur | `ngutlh ghItlhwI'` | construit | |
| couverture | `bI'reS 'echlet` | canon (2021 : « première de couverture ») | `Hev nav` |
| traducteur | `mughwI'` | canon (TKD) | |
| collection (`Imprint` de ComicInfo) | `paq chenmoHwI' 'ay'` (« branche de l'éditeur ») | construit | `tetlh` (= liste), `chov` |
| genre | `Segh` | canon (« type, sorte ») | |
| arc narratif | `lut 'ay'` (« partie d'histoire ») | construit | |
| personnages | `lut nuvpu'` | construit (`nuv` canon « personne ») | `ghotpu'` |
| équipes | `ghommey` | canon | |
| lieux | `Daqmey` | canon | |
| étiquettes (tags) | `permey` | canon (TKD : « étiquette ») | |
| notes | `QInHommey` | canon (« commentaire ») | |
| note de la communauté | `yuvwan noHghach` | construit (`yuvwan` canon 2023 « communauté », `noH` « juger ») | |
| critique | `noHghach` | construit | |
| classification d'âge | `ben patlh` (« niveau d'âge ») | construit | |
| adulte / adolescent / enfant | `nen` / `HojnIy` / `puq` | canon | `loD`, `jup`, `Qup` |
| année / mois / jour | `DIS` / `jar` / `jaj` | canon | |
| heure / minute / seconde | `rep` / `tup` / `lup` | canon | |
| bibliothèque (catalogue `.mvdb`) | `paq nojwI' qach` | canon (2016 : « bibliothèque ») ; le canon prime sur la brièveté | `nIqHom` (= logiciel) |
| marque-page | `laDmeH qoSta'` (« ruban de lecture ») | construit (`qoSta'` canon « ruban ») | `HevwI' ghItlh` |
| lu / non lu | `laDlu'pu'` / `laDlu'pu'be'` | canon | |

### Interface
| Concept | Mot | Statut | À ne pas employer |
|---|---|---|---|
| logiciel, application | `nIqHom` | canon (qep'a' 23) | `qawHaq`, `jan` |
| sécurité / pour raison de sécurité | `Hung` / `Hung meqmo'` | canon | |
| seulement | `neH` (après le nom ou le verbe qu'il restreint) | canon | |
| version | `nIqHom mI'` | canon (2018 : « numéro de version ») | `paq'yav'` |
| mise à jour | `De' chu'` | canon (STC : « mise à jour ») | |
| fenêtre | `Qorwagh` | canon | |
| écran / plein écran | `jIH` / `jIH naQ` | canon / construit | `Hoch SIrgh`, `HaSta naQ` |
| panneau (split-view) | `vol` | canon (2016 : « panneau, carreau ») | `Hol` (= langue) |
| menu | `HIDjolev` | canon | |
| bouton, touche | `leQ` | canon (TKD : « interrupteur ») | |
| icône | `Degh` | canon (« emblème, insigne ») | |
| barre d'icônes | `Deghmey tlhegh` | construit | |
| barre d'outils | `SommI'mey tlhegh` | construit (`SommI'` canon 2018 « outil ») | |
| réglette | `SeHwI'` | construit (`SeH` canon « contrôler ») | |
| panneau de configuration | `SeHlaw` | canon | |
| options, réglages | `DuHmey` | canon (« possibilité, option ») | |
| option (une case à cocher) | `DuH` | canon | |
| marge, bord | `HeH` (pl. `HeHmey`) | canon (TKD : « bord ») | |
| mise en page | `nab` (« plan ») | canon (TKDA : « plan, procédure ») | |
| brut (image telle qu'enregistrée) | `tlhol` (« être cru, non traité » : `PDF mIlloghmey tlhol`) | canon (TKD) | |
| certitude ; seulement si c'est certain | `DIch` (nom) ; `DIch tu'lu'chugh neH` | canon (TKD) | `DIch` employé comme verbe |
| suivant (dans une série) | `veb` (`tenwal veb` « la page suivante ») | canon (KGT) | |
| colonne / ligne (tableau) | `war` / `wev` | canon (2015) | `tlhegh`, `ghItlh` |
| mosaïque | `majyang mIllogh` | canon (2016 : « mosaïque ») | `Hur'` (inexistant) |
| visionneuse | `leghwI'` | construit (`legh` « voir » + `-wI'`) | |
| scanner / numériser | `HotlhwI'` / `Hotlh` | canon (TKD) | `leghwI'` (= visionneuse) |
| souris / molette | `'eQway'` / `rutlhHom` | canon (2014) | `gho` (= cercle) |
| clic / cliquer | `rIS` (nom) / `'uy` (« appuyer ») | canon | |
| clic droit / gauche / double | `nIH 'uy` / `poS 'uy` / `cha'logh 'uy` | construit | |
| glisser-déposer | `Hoq 'ej chagh` | canon (`Hoq` « tirer, faire glisser » 2020, `chagh` « lâcher ») | `tlhap 'ej lan` |
| aide | `QaH` | canon | |
| mode d'emploi | `DevmeH paq` | canon (TKW : « guide ») | `lo'laHghach QIjmeH` |
| langue | `Hol` | canon | |
| police de caractères | `ngutlh tu'qom` | canon (2018) | `Qolqo'` (inexistant), `tengwa` (quenya) |
| caractère | `ngutlh` | canon | `ghItlhwI'` |
| texte | `mu'mey` | canon | |
| thème sombre / clair | `Hurgh tu'qom` / `wov tu'qom` | construit | |
| défaut | `motlh` | canon (« habituel, standard ») | `tlhIngan` |
| manuel / automatique | `ruQlu'bogh` / `ruQbe'lu'bogh` (« contrôlé / non contrôlé à la main ») | construit sur canon `ruQ` (TKD) | `chu'moH`, `motlh` |
| personnalisé | `DawIvbogh` (« que tu choisis ») | construit | |
| succès | `Qapla'` | canon | |
| erreur | `Qagh` | canon | |
| avertissement | `ghuH` | canon (« alerte ») | |
| confirmer, valider (bouton) | `woq` ; appliquer = `lo'` | canon (2017) | `lo'laH` (= être précieux) |
| valide / invalide | `ghen` / `ghenbe'` | canon (2024) | `lugh'be'` |
| disponible / indisponible | `ngIQHa'` / `ngIQHa'be'` | canon (qep'a' 33) | |
| corrompu | `qal` | canon (TKD) | `Qaw'lu'` (= détruit) |
| endommagé | `QIH` | canon | |
| vide | `chIm` | canon | |
| inconnu | `Sovbe'lu'bogh` | construit | |
| progression | `Ser` | canon | |
| patientez | `yIloS` | canon | |
| internet / web | `'Internet` / `weQmoQnaQ` | canon (2014 / 2012) | `web`, `Internet` (sans apostrophe) |
| page web | `weQmoQnaQ tenwal` | construit | `pat` (= système) |
| connexion | `lInghach` (`lIn` « se connecter ») | construit sur canon 2018 | `ghomwI'` |
| requête / réponse | `tlhob` / `jang` | canon | |
| limite | `vuS` (verbe) / `veH` (nom) | canon | |
| mémoire (vive, de l'ordinateur) | `De'wI' qaw` (« mémoire de l'ordinateur ») | construit (`qaw` canon 2018 : « mémoire, capacité ») | `qawHaq` (= base de données) |
| être suffisant | `yap` | canon (TKD) | |
| serveur | `turwI'` | canon (2018) | |
| clé (API) | `ngaQHa'moHwI'` | canon (TNK : « clé ») | `buq` |
| message, courriel | `QIn` | canon | |
| abonnés | `DIlwI'pu'` (« ceux qui paient ») | construit | |
| argent / don | `Huch` / `nob` | canon | |
| milliard | `Saghan` | canon (qep'a' 26) | `SaD'uy'` (inexistant) |
| vidéo / audio / son | `HaSta` / `wab ta` / `wab` | canon | `video` |
| cri | `jach` | canon | `SaghQa'` |
| pilote (driver) | `jan SeHmeH nIqHom` (« logiciel pour contrôler l'appareil ») | construit | |
| brancher / allumer (appareil) | `rar` / `chu'` (« activer ») | canon | |
| nouveau / ancien (pas nouveau) | `chu'` / `ngo'`, après le nom (`GIF chu'`, `ComicInfo.xml ngo'`) | canon (homonyme de `chu'` « activer », sens donné par la place après le nom) | |
| champ (d'un formulaire) | `Daq` (pl. `Daqmey`, « emplacement ») | canon — sens étendu | |
| mais | `'ach` | canon | |
| à côté de | `retlhDaq` (`retlh` « zone à côté » + `-Daq`) | canon | |

### Actions
| Concept | Mot | Statut | À ne pas employer |
|---|---|---|---|
| ouvrir / fermer | `poSmoH` / `SoQmoH` | canon | |
| protéger | `Qan` | canon | |
| lire (un fichier) | `laD` | canon | |
| savoir, connaître, reconnaître | `Sov` | canon | |
| être complet, entier | `naQ` (`naQbe'` : incomplet) | canon | |
| être grand / plus grand que | `tIn` / `A tIn law' B tIn puS` | canon | |
| enregistrer | `choq` | canon (« préserver, sauvegarder ») | `ngaS`, `pol`, `So'` |
| annuler (bouton) | `qIl` | canon (TKD : « annuler, interrompre (un logiciel) ») | `Qaw'` (= détruire) |
| annuler une action (undo) | `vangHa'` | construit (`vang` + `-Ha'` « défaire ») | |
| refaire (redo) | `vangqa'` | construit (`-qa'` canon « refaire ») | `vI'ang` (inexistant) |
| copier | `velqa' chenmoH` (« faire une réplique ») | construit sur canon `velqa'` | `lulegh`, `chelmoH` |
| couper / coller | `pe'` / `lan` | canon | |
| supprimer | `teq` | canon (« enlever ») | |
| modifier, éditer | `DI'` (éditer) / `choH` (changer) | canon | |
| ajuster, régler | `lIS` | canon (TKD) | |
| renommer | `pong choH` | construit | |
| sélectionner / sélection | `wIv` / `wIvlu'bogh` | canon / construit | `wIv` employé comme nom |
| décocher (une option) | `wIvHa'` | construit (`wIv` + `-Ha'` canon « dé- ») | |
| rechercher / trouver | `nej` / `tu'` | canon | |
| analyser | `poj` | canon (TKD) | `lo'`, `Hut` (= neuf) |
| détecter | `jem` | canon (2017) | |
| vérifier | `'ol` | canon | |
| commencer / continuer / arrêter | `tagh` / `taH` / `mev` | canon | `taH` ou `yImev` pour « commencer » |
| interrompre (avant la fin) | `ghang` | canon (HQ 12.2) | |
| maintenir (une touche) | `'uch` (« tenir, empoigner ») — `Shift leQ yI'uch` | canon (KGT) | `HIj` (= livrer) |
| relâcher (le curseur) | `jonHa'` | canon (Star Trek V) | |
| essayer, réessayer | `nID` / `nIDqa'` | canon / construit | |
| importer, récupérer | `Suq` | canon (« obtenir ») | `lel` |
| exporter | `lel` | canon (« sortir, extraire ») | `lI'moH` |
| envoyer | `ngeH` | canon | |
| imprimer / impression | `vem chenmoH` / `vem` | construit sur canon `vem` (2015 : « impression, marque ») | `nger` (= théorie) |
| convertir | `mojmoH` (« faire devenir ») | construit | `ngor` (= tricher) |
| compresser / compression | `qoch` / `qochghach` | canon (2015 : « comprimer dans un espace ») | `machmoH` (= rendre petit, réservé à « réduire ») |
| trier / tri | `patlhmoH` / `patlhmoHghach` | canon (2018 : « classer, trier ») | `qIp` (= frapper) |
| ordre | `patlh` | canon (2024 : « ordre, comme l'ordre alphabétique ») | |
| joindre / scinder | `muv` / `rIv` | canon (TKD / 2020) | `qIpmoH` / `mobmoH` |
| recoller (rejoindre ce qui a été coupé) | `muvqa'` | construit (`muv` + `-qa'` canon « refaire ») | |
| un à un, chacun séparément | `ngIq` (après le nom : `tenwal ngIq` « page par page ») | canon (qep'a' 2011 : « un seul, chacun ») | |
| identique, doublon | `nIb` | canon (TKD : « être identique ») | `cha'logh`, `chov` |
| ignorer | `buSHa'` | canon (KGT) | |
| afficher / masquer | `'ang` / `So'` | canon | |
| déplacer | `vIHmoH` | construit (`vIH` « bouger ») | |
| insérer | `vev` | canon (2015) | |
| zoomer / dézoomer | `jo'` / `jo'Ha'` | canon (qep'a' 28) | `tIn ngo'` |
| agrandir / réduire | `tInmoH` / `machmoH` | construit | |
| enregistrer une macro / lire | `qon` / `ta'` (« accomplir ») | canon | `chenmoH` |
| macro | `HIl'aD` | canon (qep'a' 26 : « instructions pas à pas, algorithme ») | `qonta' vangmey` |
| étape | `mIw` | canon (« procédure, étape ») | `van` (= saluer) |
| recevoir | `Hev` | canon | |
| ajouter | `chel` | canon | |
| contenir | `ngaS` (« avoir à l'intérieur ») | canon | |
| rejeter, refuser | `lajQo'` | canon (KGT) | |
| perdre (être perdu) | `chIl` (`chIllu'`) | canon | |
| rester | `ratlh` | canon | |
| utiliser ; être en cours d'utilisation | `lo'` ; `lo'lu'taH` | canon (verbe ; nom `lo'` « mode » ci-dessous) | |
| peut-être / bientôt (plus tard) | `chaq` / `tugh` (`tugh yInIDqa'` « réessayez plus tard ») | canon (adverbes, en tête de phrase) | |

### Édition d'image
| Concept | Mot | Statut | À ne pas employer |
|---|---|---|---|
| couleur | `qalmuS` | canon (qep'a' 26 : « couleur ») | `rItlh`, `nguv`, `rIt` |
| pigment, peinture (outil) | `rItlh` | canon | |
| pixel | `HaStay'` | canon (qep'a' 28) | `pixelpu'`, `pIqSel` |
| résolution | `HaStay' mI'` (« nombre de pixels ») | construit | `nup` (= diminuer) |
| qualité (JPEG) | `pupghach` (`pup` « être parfait, être en haute résolution ») | construit sur canon | `nup`, `tej`, `pItlh` |
| luminosité | `wovghach` | construit (`wov` « être lumineux ») | `wov'a'` |
| contraste | `wov pIm` (« écart de lumière ») | construit | `mI'` |
| saturation | `qalmuS HoS` | construit | `ngaSpu'` |
| niveau(x) | `patlh` / `patlhmey` | canon (« niveau, échelon ») | `teblaw'` |
| point noir / blanc | `qIj vI'` / `chIS vI'` (`vI'` canon « point (géométrie) ») | construit | |
| seuil | `veH` | canon (« frontière ») | `mI'law'` |
| netteté | `Dangghach` (`Dang` « faire la mise au point ») | construit sur canon qep'a' 28 | `pe'vIl` |
| flou / flouter | `tlhoD` / `tlhoDmoH` | canon (qep'a' 23 : « être brouillé, estompé ») | `bochmoH` (= faire briller) |
| tampon | `toqwIn` | canon (2015 : « tampon ») | |
| clonage / cloner | `voyghach` / `voy` | canon (qep'a' 30 : « cloner ») | `tIng` |
| source | `Hal` | canon | `pagh wab` |
| transparent / transparence / rendre transparent | `Dem` / `Demghach` / `DemmoH` | canon (qep'a' 23 : « être transparent, incolore ») | `wovmoH` (= éclaircir) |
| effets | `wanI'mey` | canon (« phénomène, effet ») | `qaSmoHwI'mey` |
| noir et blanc / niveaux de gris | `qIj chIS je` / `qIj chIS joj patlhmey` | construit | `ghIr'a'`, `chIS HIq bIQ` |
| inversion de couleurs | `qalmuS DopmoH` (`DopmoH` « inverser ») | canon (2018) | `rItlh tlhab` |
| sépia | `ngo' qalmuS` (« couleur ancienne ») | construit, aucun mot canon | `Doqqu'` |
| mode | `lo'` | canon (TKDA : « utilisation, mode ») | `moj`, `mIw` |
| profondeur de couleur | `qalmuS patlh` | construit | `Doq chal` |
| rotation / pivoter | `jIrmoH` | canon (KGT) | `SIch`, `pItlh` |
| miroir (retourner) | `yoymoH` | canon (« retourner, inverser ») | `nuHHom` |
| horizontal / vertical | `SaS` / `chong` | canon (TKD) | `nIH-poS` |
| recadrer / masque de découpe | `pe'` / `pe'meH vel` (`vel` « couvrir, masque ») | canon / construit | |
| redresser / inclinaison | `wanmoH` / `tomghach` | construit (`wan` « être droit », `tom` « être incliné », canon 2012/2018) | `nIt chenmoH`, `'IH lo' Qu'` |
| ligne (tracé) | `yamtaw` | canon (qep'a' 24 : « ligne, trait ») | `lut`, `tlhegh` |
| angle / degré | `tajvaj` / `lawrI'` | canon | `jom`, `lat` |
| formes | `tu'qommey` | canon | `Doch chen` |
| ellipse / rectangle / flèche | `gho lang` / `letbaQ` / `naQjejHom` | canon | `gho tIn`, `meyrI'` (= carré) |
| épaisseur / remplissage | `jeDghach` / `tebghach` | construit (`jeD` « être épais », `teb` « remplir ») | `tIq` |
| poignées | `nItSolmey` | canon (qep'a' 32 : « poignée ») | `ghopmey`, `tetraghmey` |
| pipette | `qalmuS SuqwI'` | construit | `jan` |
| taille / dimensions | `tInghach` / `roDSermey` | construit / canon (qep'a' 26) | |
| largeur / hauteur / poids | `juchghach` / `'abghach` / `ngI'ghach` | construit sur les verbes de mesure canon `juch`, `'ab`, `ngI'` | `tIq`, `jen`, `'ugh` |
| pourcentage | `vatlhvI'` | canon | |
| maximum / minimum | `'aqroS` / `rav` | canon | |
| fréquence, proportion | `SubmaH` | canon (qep'a' 25) | |
| animation, image animée | `mIllogh qol` | canon (qep'a' 28) | `GIF vIHbogh`, `QummeH` |
| délai / boucle | `mIm` / `vIHtaHbogh gho` | canon | |
| palette | `qalmuS tetlh` (« liste de couleurs ») | construit | |
| commentaire | `QInHom` | canon | `QIH` (= dommage) |
| boutons gras / italique / souligné | `j` / `t` / `b` (initiales de `jeD`, `tom`, `bIng`) | construit | |

### Noms propres et langues
| Concept | Mot | Statut |
|---|---|---|
| langues terriennes | `<pays canon> Hol` : `vIraS Hol`, `jungwoq Hol`, `HayaStan Hol`… ; chinois simplifié / traditionnel = `jungwoq Hol nap` / `jungwoq Hol ngo'` ; anglais = `DIvI' Hol` | canon (noms de pays du dictionnaire) |
| japonais, néerlandais, norvégien, tamoul | `nIpon Hol`, `neDerlan Hol`, `norvegh Hol`, `tamIl Hol` | translittérés, aucun nom canon |
| sindarin / quenya | `SInDarIn Hol` / `quwenya' Hol` | canon |
| préfixe du fichier issu d'une jointure | `muvghach_xxx` | construit |
