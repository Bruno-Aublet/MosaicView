# Lexique sindarin de référence (`locales/sjn.json` et bloc `"sjn"` d'`index.html`)

S'applique aussi à la page web : le bloc `"sjn"` de `const translations` dans `index.html` emploie les mêmes mots que l'application.

Source de vérification : **Eldamo** (Paul Strack, [eldamo.org](https://eldamo.org), données [pfstrack/eldamo](https://github.com/pfstrack/eldamo), `src/data/eldamo-data.xml`, licence Creative Commons Attribution 4.0). Eldamo recense les mots de Tolkien par période (sindarin 1950-1973, noldorin 1930-1950, états plus anciens) et les néologismes des passionnés, avec leur auteur, et indique les mots qu'il déconseille.

## Règle de choix

Tolkien n'a laissé aucun vocabulaire informatique. Ordre de préférence :
1. **attesté** : mot sindarin de Tolkien (S., 1950-1973) ;
2. **attesté N.** : mot noldorin des *Etymologies* (1930-1950), non déconseillé par Eldamo ;
3. **néo** : néologisme recensé par Eldamo (Neo-Sindarin), avec son auteur ; `néo ^` = mot ancien de Tolkien (1910-1930) adapté au sindarin tardif ;
4. **construit** : assemblage d'éléments tous présents dans Eldamo (racine + suffixe attesté, composé de deux mots attestés), sans élément inventé.

Jamais un mot retenu parce qu'il est fréquent dans `sjn.json`.

Éléments de construction attestés : suffixe d'agent `-or` / `-(r)on` (« celui qui… ») ; préfixe `ad-` « de nouveau, re- » ; préfixe `ú-` « non, im- » ; impératif en `-o` (`edro` « ouvre », `daro` « arrête », attestés) ; gérondif / nom d'action en `-ad` (`tírad` « voir », attesté).

## 0. Outils de ce dossier (`conversion_tools/sindarin/`)

| Fichier | Rôle |
|---|---|
| `eldamo_sjn_index.tsv` | Index tiré d'Eldamo : sindarin (s), néo-sindarin (ns), noldorin (n), états anciens (en, g, ilk, norths). Colonnes : mot, homonyme, langue, nature, glose anglaise, statut (`attesté` / `néo` / `ancien`, `déconseillé`), marqueurs de fiabilité, mot à employer à la place, auteur du néologisme, formes attestées (fléchies, mutées), sources. Régénérable avec `build_index.py`. |
| `build_index.py` | `python -I conversion_tools/sindarin/build_index.py <eldamo-data.xml> conversion_tools/sindarin/eldamo_sjn_index.tsv` (le XML, 30 Mo, n'est pas conservé dans le dépôt : le télécharger dans un dossier vide). |
| `lookup.py` | `python conversion_tools/sindarin/lookup.py conversion_tools/sindarin/eldamo_sjn_index.tsv sortie.txt page "s:palt"` — un terme anglais cherche dans les gloses, `s:mot` cherche une forme sindarine (accents ignorés, formes fléchies comprises). Sortie UTF-8 dans un fichier. |
| `vocab_sjn.tsv` | Formes construites employées dans `sjn.json` (pluriels, composés, gérondifs, mots en `-or`), avec leurs éléments Eldamo. Une forme terminée par `-` est un verbe. Toute nouvelle forme construite s'y déclare avant d'être employée. |
| `check_sjn.py` | Contrôle lexical : chaque mot doit être un mot Eldamo (sindarin, néo-sindarin, noldorin non déconseillé), après avoir défait une mutation initiale et/ou une terminaison verbale, ou une forme de `vocab_sjn.tsv`, ou un nom propre/sigle de la liste `ALLOWED`. Les faux sens de la section 1 sont refusés (`FORBIDDEN`). Repère les mots inventés, pas les contresens. `python -I conversion_tools/sindarin/check_sjn.py conversion_tools/sindarin/eldamo_sjn_index.tsv conversion_tools/sindarin/vocab_sjn.tsv locales/sjn.json rapport.txt` |
| `run_block.sh` | Chaîne complète pour un bloc `{"chemin.de.clé": valeur}` : contrôle lexical, contrôle structurel (`conversion_tools/klingon/check_struct.py`), sauvegarde dans `%TEMP%\mosaicview_sjn_backup`, application (`conversion_tools/klingon/apply_block.py`), régénération tengwar, validation. Rien n'est écrit si un contrôle échoue. Ne touche pas à `index.html`. |

Marqueurs Eldamo : `!` néologisme de passionné, `^` mot ancien adapté, `#` attesté seulement fléchi ou en composé, `*` reconstruit, `?` / `‽` douteux, `†` archaïque ou poétique, `-` forme rejetée.

## 1. Faux sens vérifiés (mots du lexique précédent)

| Mot | Sens réel (Eldamo) | Concept pour lequel il était employé | Occurrences dans `sjn.json` |
|---|---|---|---|
| `randir` / `rendir` | **vagabond, pèlerin** (S.) | image | 118 / 54 |
| `pen` | **sans** (préposition) ; **quelqu'un** (pronom) | plein (écran), divers | 46 |
| `aew` | **petit oiseau** (S.) | erreur | 36 |
| `tell` / `telleir` | **dernier** (néo) ; « fin » (ilk.) | page | 33 / 37 |
| `taur` | **forêt** (S.) | dossier | 30 |
| `ambar` / `embar` | n'existe pas en sindarin (quenya : « monde ») | fichier | 34 / 8 |
| `haedh` | **enclos** (S.) | Annuler | 23 |
| `deleb` | **horrible, abominable** (N.) | Supprimer | 22 |
| `harthad` | **espoir** (N.) | visionneuse | 22 |
| `minui` | **premier** (S.) | suivant | 17 |
| `aen` | **devrait, pourrait** (S.) | oui | 15 |
| `lú` / `lû` | **moment, occasion** (néo) | série | 18 |
| `lath` | **lanière de cuir** (néo) ; « année » (gn.) | ligne | 18 |
| `thamb` | **raide, rigide** (ᴱN., déconseillé) | bibliothèque | 10 |
| `thala` | **vaillant, ferme** (N.) | droit, horizontal | 10 |
| `lain` | **fil de chaîne** (S.) ; **libéré** (néo) | plein (écran) | 11 |
| `hannas` | **compréhension, intelligence** (N.) | résumé | 7 |
| `hair` | **gauche** (main, néo) | maintenir le clic | 8 |
| `tâch` | **ferme, raide** (ilkorin) | maintenir | 7 |
| `host` | **grosse (144)** (N.) | fenêtre | 3 |
| `formen` | absent ; `forn` = **nord** (S.), « droite » seulement en N. | droite | 17 |
| `Nothrannath`, `Tirhor`, `Avedui`, `Adrevedui`, `Pado`, `Honom`, `Orthertha`, `Nedhoro`, `govaded`, `thúr`, `Ceno`/`Cenor`, `revail`, `nudh`/`nuidad`, `bethleg(in)`, `Golover`, `pedid`, `thennad`, `rlinn`, `Sada` | **absents d'Eldamo** (inventés) | métadonnées, éditeur, undo, redo, coller, copier, appliquer, cliquer, lot, menu, scanner, doublon, compression, marque-page, registre, requête, séquence, mode, télécharger | 15, 6, 7, 18, 20, 10, 9, 38, 15, 7, 8, 1, 5, 21, 3, 6, 33, … |

Ces mots touchent **418 des 1 721 valeurs** de `sjn.json`, sans compter les autres mots inventés du fichier (pré-scan complet à l'étape 2).

Mots du lexique précédent **confirmés** : `egor` « ou » (S.), `pain` « tous » (pluriel de `pân`, S.), `bain` « beau » (S. — à ne pas confondre avec `pain`), `dolen` « caché » (S.), `cen-` « voir » (S.), `harvo` « main gauche » (S.), `iant` « pont » (S.), `men` « route, chemin » (S.), `nath` « toile » (N.), `tîw` « lettres » (pluriel de `têw`, S.), `tirith` « garde, veille » (S.), `toltha-` « aller chercher » (N.), `ú-` / `û` « non » (S.).

## 2. Lexique

### Fichiers, stockage, interface
| Concept | Mot | Statut (source) | À ne pas employer |
|---|---|---|---|
| fichier | `parlas` (pl. `perlais`) | néo ! (Roman Rausch : « parchemin, document ») | `ambar`, `embar`, `vilth`, `bâr` |
| dossier, répertoire | `colch` (pl. `cylch`) | néo ^ (Súlien : « boîte, coffre ») | `taur` (= forêt) |
| archive (CBZ, CBR…) | `levn` | néo ^ (Paul Strack : « gerbe, liasse ») | |
| bibliothèque (`.mvdb`) | `partham` | néo ! (Ellanto : « bibliothèque ») | `Thamb` (= rigide) |
| page | `palt` (pl. `pelt`) | néo ^ (« feuille large ; page de livre ») | `tell`, `telleir` (= dernier), `peth` (= mot) |
| image | `em` | néo ! (Fiona Jallings : « image, tableau ») — pluriel à fixer | `randir`/`rendir` (= vagabond) |
| icône | `aerem` | néo ! (Vyacheslav Stepanov : « icône ») | `randir tithen`, `tinconi`, `Tengwa` |
| document | `parlas` | néo ! | |
| liste, menu | `narth` | néo ! (Ellanto : « liste ») | `thúr` (inventé), `menu` |
| fenêtre | `henneth` (pl. `hennith`) | attesté (S., *Henneth Annûn*) | `host` (= 144) |
| visionneuse | `Tírad` | attesté (gérondif de `tíra-` « voir », forme `tírad` attestée) | `Harthad` (= espoir) |
| mot | `peth` | attesté | |
| nom | `eneth` | attesté | |
| numéro, nombre | `noth` | néo ^ (Elaran) | |
| titre | `enas` | néo ^ | |
| langue | `lam` | attesté | |
| police de caractères | `tîw` (« lettres ») | attesté (pluriel de `têw`) — sens étendu | `tengwa` (quenya) |
| clé (API) | `ledil` | néo ! (Paul Strack : « clé ») | |
| registre Windows | `narth Windows` (« liste de Windows ») | construit | `Golover` (inventé) |
| marque-page | `taith` (« signe, marque ») | attesté — sens étendu | `bethleg` (inventé) |

### BD et métadonnées
| Concept | Mot | Statut (source) | À ne pas employer |
|---|---|---|---|
| série | `lîr` (« rangée, ligne ») | attesté # — sens étendu | `Lú` (= moment) |
| éditeur (publisher) | `edannor` | construit (`edanna-` « publier », néo ! Cellindir + agent `-or`) | `Tirhor` (inventé) |
| résumé | `narn thent` (« récit court ») | construit (`narn` « récit » + `thent` « court », attestés) | `Hannas` (= intelligence) |
| métadonnées | `golu` (« savoir ») | néo ^ — sens étendu ; écarté : `siniath` « nouvelles » (N.) | `Nothrannath` (inventé) |
| couverture | `gwael` | néo ^ (Paul Strack : « fourreau, étui, couverture ») | |
| récit, histoire | `narn` | attesté | |
| scénariste, auteur | `teithor` | construit (`teitha-` « écrire », attesté + `-or`) | |
| dessinateur | `emmor` | construit (`emma-` « peindre, dessiner », néo ! Arael + `-or`) | |
| artiste | `maeron` | attesté | |
| année / mois / jour | `în` / `ranoth` / `aur` | attesté / néo / attesté | |
| doublon | `gwanon` (« jumeau ») | attesté | `revail` (inventé) |
| identique | `ain` | néo ! (Fiona Jallings, Elaran) | |

### Actions
| Concept | Mot (impératif de bouton) | Statut (source) | À ne pas employer |
|---|---|---|---|
| ouvrir | `Edro` | attesté (`edra-`, impératif attesté) | |
| fermer | `Holo` | attesté (`hol-`) | |
| enregistrer, sauvegarder | `Haudho` | néo ! (Paul Strack : `haudha-` « mettre en réserve, sauvegarder, enregistrer ») | `Tirith` (= garde) |
| Annuler (bouton) | `Daro` (« arrête ! ») | attesté | `Haedh` (= enclos) |
| annuler une action (undo) | `Dangaro` | attesté (`dangar-` « défaire ») | `Avedui` (inventé) |
| refaire (redo) | `Adgaro` | construit (`ad-` « de nouveau » + `car-` « faire », mutation douce) | `Adrevedui` (inventé) |
| supprimer | `Haedo` | néo ! (Elaran : `haeda-` « retirer, éloigner ») | `Deleb` (= horrible) |
| couper / recadrer | `Risto` | néo ^ (`rista-` « couper ») | |
| copier | `Echado adu` (« fais un double ») | construit (`echad-` « façonner » + `adu` « double », attestés) ; aucun mot dans Eldamo, et Eldamo ne recense pas de suffixe causatif `-tha` isolé | `Honom` (inventé), `Adutho`, `Teitho` (= écris) |
| coller | `Panno` | néo ^ (`panna-` « placer, poser ») | `Pado` (inventé), `Tacho` |
| appliquer | `Caro` (« fais ») | attesté (`car-`, *caro den i innas lín*) | `Orthertha` (inventé) |
| sélectionner, choisir | `Cilo` / nom `cilith` | néo ! (Fiona Jallings ; Elaran) | |
| chercher / trouver | `Cesto` / `hir-` | néo ! (Fiona Jallings) / néo ! | |
| examiner, scanner | `Cetho` ; appareil `cethor` | néo ! (Fiona Jallings : `ceth-` « examiner ») ; `cethor` construit | `Ceno`, `Cenor` |
| télécharger, importer | `Toltho` (« va chercher ») | attesté N. (`toltha-`) | `Sada` (inventé) |
| envoyer, exporter | `Menno` | néo ! (Fiona Jallings : `menna-`) | |
| publier | `edanna-` | néo ! (Cellindir) | |
| demander, requête | `mabed-` / nom `mabed` | néo ! (Fiona Jallings) | `pedid` (inventé) |
| cliquer | `Apho` (« touche ») | néo ! (`apha-` « toucher ») | `Nedhoro` (inventé) |
| clic gauche / droit | `Apho na harvo` / `Apho na forvo` | construit (`harvo` / `forvo` « main gauche / droite », attestés) | `formen` |
| maintenir (touche, clic) | `Hebo` (« garde ») | attesté (`heb-` « garder ») | `Tach`, `hair` |
| glisser / déposer | `Logo` / `Dodo` | néo ! (`log-` « traîner ») / néo (`dod-` « tomber ») | |
| afficher / masquer | `Tano` / `Toblo` | néo ! / néo ^ | |
| déverrouiller | `Ledio` | néo ! (Röandil : `ledia-`) | |
| lire | `Henno` | néo ! (Elaran : `henna-`) | |
| tourner (rotation) | `Perno` | néo ! (Hialmr : `perna-`) | |
| miroir | `cenedril` | attesté | |
| scinder | `thag-` | néo ! (Gilruin : « fendre ») | |
| fusionner, joindre | `ianna-` | néo ! (Paul Strack : « joindre, relier ») | |
| trier | `lhid-` | néo ^ (« trier, tamiser ») | |
| changer, convertir | `gwista-` / nom `gwist` | néo ! (Paul Strack) | |
| compresser / compression | `gweltha-` / `gwelthad` | néo (« presser ») ; nom construit | `nudh`, `nuidad` (inventés) |
| commencer / finir | `iesta-` / `tel-` | néo ! (Fiona Jallings) | |
| attendre | `Dartho` | attesté N. (`dartha-`) | |
| traduire | `nevia-` | néo ! (Fiona Jallings) | |
| enregistrer une macro / macro | `haudha-` / `haudhad` (« enregistrement ») | néo ! + gérondif | `i thennad tirith` |
| traitement par lot | `hoth` (« groupe, foule ») | attesté — sens étendu | `govaded` (inventé), `Lotë` |

### États, messages
| Concept | Mot | Statut (source) | À ne pas employer |
|---|---|---|---|
| erreur | `dŷl` (« méprise, erreur ») | attesté | `Aew` (= petit oiseau), `Raul`, `Ahared`, `Raeg`, `Gûr` (= mort) |
| avertissement | `gored` | construit (gérondif de `gor-` « avertir », attesté) | `gorth` (= mort), `gorn` (= vénéré) |
| danger | `rach` | néo ! (Elaran) | |
| aide | `grest` | néo | |
| terminé | `carnen` | néo | |
| fin | `methed` | attesté | |
| oui / non | `Naw` / `Baw` (« non, ne fais pas ») ou `Û` | néo ! / attesté | `Aen` (= devrait) |
| nouveau | `sain` | néo ^ | |
| vide | `lost` | attesté # | |
| caché | `dolen` | attesté | |
| secret | `hûl` | attesté | |
| assez, suffisant | `far` (`ú-far` « pas assez ») | attesté N. | `faer` (= esprit) |
| endommagé, abîmé | `feithannen` | participe de `feitha-` « détruire, abîmer » (néo ^) | |
| morceau, partie | `sath` (pl. `saith`) | néo ! (Elaran) | |
| format (d'image, de fichier) | `cant` (« forme ») | attesté | |
| mémoire (de l'ordinateur, RAM) | `rened` | construit (gérondif de `ren-` « se souvenir », néo ! David Salo) | `golu` (= savoir, réservé à « métadonnées ») |

### Navigation, affichage
| Concept | Mot | Statut (source) | À ne pas employer |
|---|---|---|---|
| suivant | `abor` | néo ^ (Paul Strack : « suivant ») | `minui` (= premier) |
| précédent | `nui` (« avant ») | néo ^ — aucun adjectif dans Eldamo | |
| premier / dernier | `minui` / `medui` | attesté | `tell` |
| plein écran | `henneth bant` (« fenêtre pleine ») | construit (`pant` « plein », attesté, mutation douce) | `lain pen`, `Pân-sgrîn` |
| mode (de lecture…) | `rhaed` (« façon particulière ») | attesté — sens étendu | `bâd rlinn` (`bâd` = route, `rlinn` inventé) |
| ligne | `lîr` | attesté # | `lath` (= lanière) |
| droit / horizontal | `taer` / `laidh` | attesté N. / néo ! (Ellanto) | `thala` (= vaillant) |
| double | `edaid` | attesté | |
| petit / grand | `pîn` / `beleg` | attesté | |
| hauteur / longueur | `hallas` / `annas` | néo ! | |
| couleur | `crui` | néo | |
| lumière, luminosité / obscurité | `calad` / `môr` | attesté | |
| flou | `hîth` (« brume ») | attesté — sens étendu | |
| net, aigu | `maeg` | attesté | |
| transparent (« de verre ») | `heledhren` | néo ! | |
| niveau | `lad` | attesté | |
| valeur, qualité | `meiras` | néo ! (Paul Strack) | |

### Web, réseau
| Concept | Mot | Statut (source) | À ne pas employer |
|---|---|---|---|
| web | `nath` (« toile ») | attesté N. | `had` |
| adresse (URL) | `men` (« chemin ») | attesté — sens étendu | `egor` (= ou), `genediad` |
| lien | `lîf` | attesté # (« lien, jointure ») | |
| connexion | `golain` | néo ! (Sami Paldanius : « connexion, relation ») | |

### Mots-outils
| Concept | Mot | Statut |
|---|---|---|
| et / ou | `ar` / `egor` | attesté |
| de (provenance) / avec / sans | `o` / `na` / `pen` | attesté |
| tous | `pain` (pluriel de `pân`) | attesté |
| non-, im- | `ú-` | attesté |
| de nouveau, re- | `ad-` | attesté |
| seulement / aussi | `ero` / `eithro` | attesté |
| maintenant | `hí` | attesté |
| dans, vers l'intérieur | `mî` | néo ! |

### Termes fixés lors de la retraduction complète
| Concept | Mot | Statut (source) | À ne pas employer |
|---|---|---|---|
| BD, livre ; volume | `parf` (pl. `perf`) | attesté N. | `comin` |
| mosaïque | `sarnem` (« image de petites pierres ») | construit (`sarn` + `em`) | `cabed` |
| vignette | `emig` (« petite image ») | construit (`em` + diminutif `-ig`) | |
| sous-dossier | `nugolch` (pl. `nugylch`) | construit (`nu` « sous » + `colch`) | |
| base de données (`.mvdb`) | `thond-golu` (« fondement de savoir ») | construit | |
| colonne (tableau, barre) | `tagol` (pl. `tegil`) | attesté (« poteau ») | |
| curseur, réglette ; niveaux | `lad` | attesté (« plaine, niveau ») | |
| résolution | `noth till` (« nombre de points ») | construit | `maegas` (= netteté) |
| netteté / netteté adaptative | `maegas` / `maegas cammol` | construit (`maeg` + `-as`) / néo ! (`camma-`) | |
| luminosité / contraste / saturation | `calad` / `egelas` / `cruith` | attesté / construit (`egel` + `-as`) / néo | |
| profondeur de couleur | `nûras crui` | construit (`nûr` N. « profond » + `-as`) | |
| effet(s) | `carth` (pl. `gerth`) | attesté (« action ») | |
| taille / poids / largeur / hauteur | `daeras` / `lungas` / `landas` / `hallas` | construits (`-as`) / néo ! | |
| transparence / transparent | `heledhras` / `heledhren` | construit / néo ! | |
| redressement / redresser | `taerechad` / `Echado taer` | construit (`taer` + `echad`) | `tírada-` (néo, mais entre en collision avec `Tírad`) |
| rotation | `pernad` | gérondif de `perna-` | |
| licence | `theinas` (pl. `theinais`) | néo ! (« loi, règle ») | |
| application, logiciel | `maethas` | construit (`maetha-` « utiliser » + `-as`) | |
| outil, fonction | `carf` | néo ! (Paul Strack) | |
| application (bouton Appliquer) | `Caro` | attesté | |
| valider | `Cavo` (« accepte ») | néo ! (`cav-`) | |
| presse-papiers / corbeille | `colch-pannad` / `colch-haedad` | construits | |
| navigateur web / messagerie | `maethas-nath` / `maethas-tain` | construits | `'aud` |
| mail, courriel | `tain` | néo ! (« lettre, missive ») | |
| tableur | `maethas-narth` | construit | |
| scanner (pl.) | `cethor` (pl. `cethyr`) | construit (`ceth-` + `-or`) | `Cenor` |
| macro ; étape (pl.) | `haudhad` ; `pâd` (pl. `paid`) | gérondif ; attesté (« pas ») | |
| info-bulle, aide | `golu` / `grest` | néo ^ / néo | |
| mode d'emploi | `Grest` | néo | |
| récent | `gwain` | attesté | |
| journal (log) | `narth` | néo ! | |
| tags | `pethath` | construit (`peth` + collectif `-ath`) | `teith` (= marque-pages) |
| marque-pages (pl.) | `teith` | pluriel de `taith` | |
| poignée(s) | `manc` (pl. `mainc`) | néo | |
| clic (nom) | `aphad` | gérondif de `apha-` | |
| molette | `rind` (« cercle ») | attesté — sens étendu | |
| langues fictives | `lem ôl` (« langues de rêve ») | construit (`lam` pl. `lem` + `ôl` N. « rêve ») | `ñúrin` |
| noms des langues (aide, page web) | endonymes (`Deutsch`, `Français`, `Nihongo`…) traités comme noms propres | — | noms inventés en `-eg` |

## 3. Grammaire à respecter lors de la retraduction

- **Mutation douce** après l'article `i` et dans les composés : `p→b`, `t→d`, `c→g`, `b→v`, `d→dh`, `g→` (disparaît), `m→v` (ex. `palt` → `i balt` « la page »). Les mutations seront prises en compte par le contrôleur lexical (à écrire).
- **Pluriel par affection vocalique** : `a→e` (finale `a→ai`), `o→y`/`e`, `e→i` en syllabe finale (`palt → pelt`, `parlas → perlais`, `henneth → hennith`). L'article pluriel est `in` (qui mute aussi le mot).
