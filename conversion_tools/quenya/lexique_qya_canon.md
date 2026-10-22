# Lexique quenya de référence (`locales/qya.json` et bloc `"qya"` d'`index.html`)

S'applique aussi à la page web : le bloc `"qya"` de `const translations` dans `index.html` (y compris `lang_names`) emploie les mêmes mots que l'application.

Source de vérification : **Eldamo** (Paul Strack, [eldamo.org](https://eldamo.org), données [pfstrack/eldamo](https://github.com/pfstrack/eldamo), `src/data/eldamo-data.xml`, licence Creative Commons Attribution 4.0). Eldamo recense les mots de Tolkien par période (quenya 1950-1973, middle quenya des *Etymologies* 1930-1950, early quenya 1910-1930) et les néologismes des passionnés (néo-quenya), avec leur auteur, et indique les mots qu'il déconseille.

## Règle de choix

Tolkien n'a laissé aucun vocabulaire informatique. Ordre de préférence :
1. **attesté** : mot quenya de Tolkien (Q., 1950-1973) ;
2. **attesté MQ** : mot des *Etymologies* (1930-1950), non déconseillé par Eldamo ;
3. **néo** : néologisme recensé par Eldamo (néo-quenya), avec son auteur ;
4. **construit** : assemblage d'éléments tous présents dans Eldamo (racine + suffixe attesté, composé de deux mots attestés), sans élément inventé. Chaque forme construite est déclarée dans `vocab_qya.tsv`.

Jamais un mot retenu parce qu'il est fréquent dans `qya.json`.

## 0. Outils de ce dossier (`conversion_tools/quenya/`)

| Fichier | Rôle |
|---|---|
| `eldamo_qya_index.tsv` | Index tiré d'Eldamo : quenya (q), néo-quenya (nq), middle quenya (mq), early quenya (eq). Colonnes : mot, homonyme, langue, nature, glose anglaise, statut (`attesté` / `néo` / `ancien`, `déconseillé`), marqueurs de fiabilité, mot à employer à la place, auteur du néologisme, formes attestées (fléchies, en composé), sources. Régénérable avec `build_index.py`. |
| `build_index.py` | `python -I conversion_tools/quenya/build_index.py <eldamo-data.xml> conversion_tools/quenya/eldamo_qya_index.tsv` (le XML, 30 Mo, n'est pas conservé dans le dépôt : le télécharger dans un dossier vide). |
| `lookup.py` | `python conversion_tools/quenya/lookup.py conversion_tools/quenya/eldamo_qya_index.tsv sortie.txt page "q:parma"` — un terme anglais cherche dans les gloses, `q:mot` cherche une forme quenya (accents et tréma ignorés, formes fléchies comprises). Sortie UTF-8 dans un fichier. |
| `vocab_qya.tsv` | Formes construites employées dans `qya.json` (composés, mots préfixés), avec leurs éléments Eldamo. Toute nouvelle forme construite s'y déclare avant d'être employée ; elle accepte ensuite les terminaisons quenya. |
| `check_qya.py` | Contrôle lexical : chaque mot doit être un mot Eldamo (quenya, néo-quenya, middle quenya non déconseillé) ou une forme attestée, au besoin après avoir retiré jusqu'à trois terminaisons empilées (pluriels `-r`/`-i`/`-li`, cas `-o`/`-n`/`-nna`/`-llo`/`-ssë`/`-nen`/`-va`, possessifs `-nya`/`-lya`/`-rya`…, pronoms sujets `-lyë`/`-n`, temps et participes `-uva`/`-në`/`-ina`/`-la`, gérondif `-ië`, nom verbal `-lë`, agent `-ndo`/`-mo`, instrument `-ma`), ou une forme de `vocab_qya.tsv`, ou un nom propre/sigle de la liste `ALLOWED`. Un préfixe (`at(a)-`, `en-`, `yu-`) n'est accepté que dans une forme déclarée. Les `k`/`q` du middle quenya valent `c`/`qu`. Les faux sens de la section 1 sont refusés (`FORBIDDEN`), comme les mots anglais ou français courants qui coïncident avec une forme quenya (`FOREIGN`). Repère les mots inventés, pas les contresens. `python -I conversion_tools/quenya/check_qya.py conversion_tools/quenya/eldamo_qya_index.tsv conversion_tools/quenya/vocab_qya.tsv locales/qya.json rapport.txt` |
| `run_block.sh` | Chaîne complète pour un bloc `{"chemin.de.clé": valeur}` : contrôle lexical, contrôle structurel (`conversion_tools/klingon/check_struct.py`), sauvegarde dans `%TEMP%\mosaicview_qya_backup`, application (`conversion_tools/klingon/apply_block.py`), régénération tengwar, validation. Rien n'est écrit si un contrôle échoue. Ne touche pas à `index.html`. |

Marqueurs Eldamo : `!` néologisme de passionné, `^` mot ancien adapté, `#` attesté seulement fléchi ou en composé, `*` reconstruit, `?` / `‽` douteux, `†` archaïque ou poétique, `-` forme rejetée.

## 1. Faux sens vérifiés (mots du lexique précédent)

| Mot | Sens réel (Eldamo) | Concept pour lequel il était employé | Valeurs de l'ancien `qya.json` |
|---|---|---|---|
| `fána` | **blanc** ; « nuage » (MQ) | image | 72 |
| `Russë` | **chevelure rousse** | erreur | 45 |
| `quetta` | **mot** | fichier (forme minoritaire) | 37 |
| `cenil`, `Cenila` | absents d'Eldamo | scanner | 27 |
| `Avahaila` | absent d'Eldamo | Annuler (bouton) | 24 |
| `Pata` | **marcher** | coller | 23 |
| `tengwa` | **lettre écrite** | police de caractères | 23 |
| `nutta` | **virgule** (néo) | compresser | 23 |
| `alda` | **arbre** | dossier | 22 |
| `Nai` | **peut-être**, « puisse-t-il être » | oui | 17 |
| `windo` | absent d'Eldamo | panneau | 17 |
| `mossë` | absent d'Eldamo | mosaïque | 15 |
| `Samna` | **trou creusé** ; « poteau » (MQ) | copier | 15 |
| `Nurta` | **cacher** | annuler une action (undo) | 12 |
| `Centuva` | absent d'Eldamo | visionneuse | 12 |
| `Parmë` | absent d'Eldamo | résumé | 11 |
| `Lindelë` | **musique** (EQ, déconseillé) | série | 9 |
| `Métanótë` | absent d'Eldamo | métadonnées | 7 |
| `Hyarya` | **gauche** (main, MQ) | maintenir | 7 |
| `Oia` | **éternel** | OK | 7 |
| `Antanor`, `Parmavorn` | absents d'Eldamo | éditeur, bibliothèque | 4 / 4 |
| `renta` | **rappeler** (néo) | ligne | 4 |
| `thala` | absent d'Eldamo (sindarin) | droit, horizontal | 4 |
| `parma` | **livre, document** | fichier **et** page | 322 |

Mots du lexique précédent **confirmés** : `latya-` « ouvrir », `hya` « ou », `imya` « identique », `cilmë` « choix », `yanta` « pont » / `yanta-` « joindre » (néo), `hosta-` « rassembler », `tamma` « outil », `ilyë` « tous », `natse` « toile, réseau » (MQ), `tulya-` « amener », `harya-` « tenir, posséder », `lá` « non ».

## 2. Lexique

### Fichiers, stockage, interface
| Concept | Mot | Statut (source) | À ne pas employer |
|---|---|---|---|
| fichier | `cómë` (pl. `cómi`) | néo ! (Yitzik Penzev : « fichier ») | `parma`, `quetta` |
| dossier, répertoire | `colca` (pl. `colcar`) | néo (« boîte ») | `alda` (= arbre) |
| sous-dossier | `nucolca` | construit (`nu` « sous » + `colca`) | |
| dossier racine | `talma` (« base, fondement ») | attesté | |
| dossier maître (bibliothèque) | `héra colca` | `héra` « principal » (MQ) | |
| archive (CBZ, CBR…) | `limna` (« gerbe ») | néo — sens étendu, comme le sindarin `levn` | |
| BD, comic | `emmaparma` | néo ! (Luinyelle : « livre d'images ») | `comicon` |
| livre ; volume (métadonnée) | `parma` | attesté | `corda` (= temple) |
| page | `parmalassë` (pl. `parmalassi`) | néo ! (Sami Paldanius : « feuille de livre ») | `parma` |
| page web | `natse-parmalassë` | `natse` + `parmalassë` | |
| image | `emma` (pl. `emmar`) | attesté | `fána` (= blanc) |
| vignette | `emmincë` (pl. `emminci`) | construit (`emma` + diminutif `-incë`) | |
| mosaïque | `saremma` | construit (`sar` « petite pierre » + `emma`) | `mossë` |
| icône | `airemma` | néo ! (Vyacheslav Stepanov) | |
| fenêtre | `lattin` (pl. `lattini`) | néo (préféré par Eldamo) | |
| plein écran | `quanta lattin` | attesté (`quanta` « plein ») | |
| panneau (split-view) | `permë` (« moitié, côté », pl. `permi`) | néo ! (Röandil) | `windo` |
| interface | `cendelë` (« visage ») | attesté — sens étendu | |
| visionneuse | `Cendama` | construit (`cenda-` « lire, observer » + instrumental `-ma`) | `Centuva` |
| liste, menu | `nyarda` | néo ! (Ellanto : « liste ») | `cilmer`, `menu` |
| colonne, barre d'icônes | `tarma` (« pilier ») ; `tarma airemmaron` | attesté | |
| barre de statut | `tië istaléva` (« ligne d'information ») | construit | |
| barre d'outils de la visionneuse | `tië tammaron` | construit | |
| bouton | `cortangwa` | néo ! (Parmandil) | |
| réglette, curseur | `lesta` (« mesure ») | MQ | |
| masque (de découpe) | `fanwa` (« voile, écran ») | attesté | |
| presse-papiers | `tatyahaura` | construit (`tatya-` + `haura` « réserve ») | |
| corbeille | `haitacolca` | construit (`haita-` + `colca`) | |
| base de données | `istalehaura` | construit (`istalë` + `haura`) | |
| bibliothèque (`.mvdb`) | `parmasan` | néo ! (Ellanto) | `Parmavorn` |
| nom | `essë` (pl. `essi`) | attesté | |
| titre | `essë` | attesté | |
| numéro, nombre | `nótë` (pl. `nóti`) | MQ | |
| mot | `quetta` | attesté | |
| langue | `lambë` (pl. `lambi`) | attesté | |
| langues fictives | `lambi lóreo` (« langues du rêve ») | attesté (`lórë`) | `ñúrin` |
| police de caractères | `sallë` (pl. `salli`) | néo ! (Orondil : « police, style d'écriture ») | `tengwa` |
| lettre, caractère | `tengwa` (pl. `tengwar`) | attesté | |
| clé (API, touche) | `latil` (pl. `latili`) | néo ! (Helge Fauskanger) | |
| registre Windows | `nyarda Windows` | construit | `Ondócola` |
| marque-page | `parmatehta` | construit (`parma` + `tehta` « marque ») | `lambetengwe` |
| code (source) | `tengwesta` (« système de signes ») | attesté | |
| logiciel, application, outil | `tamma` | attesté | |
| pilote (de scanner) | `tulyatamma` | construit (`tulya-` « conduire » + `tamma`) | |
| navigateur web | `natsetamma` | construit | |
| ordinateur, système | `nótar` | néo ! (NQ-Wiki) | |
| journal (log) ; historique | `nyárë` | attesté (« récit, histoire ») | |
| journal d'erreurs ; inattendu | `nyárë loimaron` ; `ú apacen` (« sans prévision ») | construit ; attestés (`ú` « sans », `apacen` « prévoyance ») | |
| démarrer | `yesta-` | néo ! (Ryszard Derdzinski) | `astuvu-` (déconseillé) |
| dix / vingt | `quëan` / `yuquain` | attesté / néo ! (EruannoVG) | `yucainen`, `yúquëan` (déconseillés) |
| options, réglages | `cilmi` | attesté (pl. de `cilmë`) | |
| configuration | `partalë` | néo (`parta-` « arranger ») | |
| thème (clair/sombre) | `panwë` | néo ! (Röandil) | |
| paramètre de format | `nostalë` (« sorte, type ») | néo | |
| zone | `latsë` | néo ! (Tamas Ferencz) | |
| tableau (Excel) | `paluhta` | néo ! (Helge Fauskanger) | |
| disque | `rinco` | néo | |
| section (de l'aide) | `putton` | néo ! (Paul Strack, Luinyelle) | |

### BD et métadonnées
| Concept | Mot | Statut (source) | À ne pas employer |
|---|---|---|---|
| métadonnées | `istalë` | néo ! (NQ-Wiki : « information ») | `Métanótë` |
| série | `téma` | attesté (« série, rangée ») | `Lindelë` (= musique) |
| éditeur (maison) | `etantamo` | construit (`etanta-` « publier », néo ! Luinyelle + agent `-mo`) | `Antanor` |
| éditeur (personne) | `tengwistando` | construit (`tengwista-` « éditer », néo + `-ndo`) | |
| résumé | `nauca quenta` (« récit raccourci ») | attesté | `Parmë` |
| couverture | `túpo` | néo (« couvercle ») | |
| scénariste, auteur | `tecindo` | néo ! (Mans Bjorkman) | |
| dessinateur | `emmatando` | construit (`emmata-` « dessiner », néo + `-ndo`) | |
| encreur | `mórondo` | construit (`móro` « encre », néo + `-ndo`) | |
| coloriste / lettreur | `quilyando` / `tengwando` | construits (`-ndo`) | |
| traducteur ; traduire | `lambelangando` ; `lambelanga-` | néo ! (Paul Strack) + `-ndo` | |
| artiste | `maitar` | attesté | |
| personnages | `eminor` | néo ! (Sami Paldanius) | |
| collection (imprint) | `combë` | attesté | |
| genre | `nostalë` | néo | |
| format (de BD) | `canta` (« forme ») | attesté | |
| tags | `tannar` (« signes ») | attesté | `tehtar` |
| arc narratif | `lúva quentava` (« courbe du récit ») | attesté | |
| année / mois / jour | `löa` / `asta` / `aurë` | attesté | |
| mois (noms) | `Narvinyë`, `Nénimë`, `Súlimë`, `Víressë`, `Lótessë`, `Nárië`, `Cermië`, `Úrimë`, `Yavannië`, `Narquelië`, `Hísimë`, `Ringarë` | attesté (calendrier de l'Appendice D) | |
| doublon | `onóna` (« jumeau ») | attesté | |
| identique | `imya` | attesté | |
| site web ; web | `natsemen` ; `natse` | néo ! (NQ-Wiki) ; MQ | `wilwarin` |
| adresse (URL) | `tengessë` | néo ! (Boris Shapiro) | `tie` |
| lien | `limë` | attesté | |
| connexion | `himyalë` | néo ! (NQ-Wiki) | `lelya` |
| requête (API) | `iquista` | néo | |
| macro | `carmen` (« recette ») | néo ! (Orondil) | |
| étape (de macro) | `pat` (pl. `pati`) | néo (Paul Strack) | |

### Actions (impératif de bouton = radical du verbe)
| Concept | Mot | Statut (source) | À ne pas employer |
|---|---|---|---|
| ouvrir | `Latya` | attesté | |
| fermer | `Holya` | attesté | |
| enregistrer, sauvegarder | `Hepa` | néo ! (Ales Bican : `hep-` « garder ») | |
| Annuler (bouton) | `Nuhta` (« arrêter avant terme ») | attesté | `Avahaila` |
| annuler une action (undo) | `Nancara` | attesté (`nancar-` « défaire ») | `Nurta` (= cacher) |
| refaire (redo) | `Encara` | construit (`en-` « de nouveau » + `car-`) | `Ata-carë` |
| supprimer | `Haita` | néo ! (Petri Tikka : « retirer ») | |
| couper, recadrer | `Rista` | attesté | |
| copier | `Tatya` (« doubler ») | MQ | `Samna` |
| coller | `Caita` (« poser ») | attesté | `Pata` (= marcher) |
| insérer | `mitta-` | attesté | |
| appliquer, valider | `Cara` (« fais ») | attesté | |
| sélectionner ; sélection | `Cila` ; `cilmë` | néo ! / attesté | |
| décocher (une option) | `Áva cila` (« ne choisis pas ») | attesté (`áva`) + néo ! | `Áva cilë` |
| ignorer, laisser de côté | `hehta-` | attesté (« mettre de côté, exclure ») | |
| inverser (sélection) ; remplacer | `Quapta` (« échanger ») | néo | |
| désélectionner | `Lerya` (« libérer ») | attesté | |
| chercher ; trouver | `Cesa` ; `hir-` | attesté | |
| scanner (numériser) ; scanner (appareil) | `Henta` ; `hentama` | attesté (« examiner, lire, scanner ») ; construit (`-ma`) | `Cenila`, `Cenil` |
| télécharger, importer | `Tulta` (« faire venir ») | attesté | |
| exporter, envoyer | `Menta` | attesté | |
| cliquer ; clic | `Toca` ; `tocië` | néo ! (Orondil : `toc-`) | |
| clic gauche / droit | `Toca hyarmanen` / `Toca formanen` ; `hyarma tocië` / `forma tocië` | attesté (`hyarma`, `forma`) | `Hyarya` |
| maintenir (clic, touche) | `Harya` | attesté | `Hyarya` (= gauche) |
| glisser / déposer | `Luca` / `Lanta` | néo / attesté | |
| déplacer (un objet) | `Rúma` | attesté (`rum-`) | `leva` (intransitif) |
| afficher / masquer | `Tana` / `Cuva` | attesté | |
| lire (une BD) ; lire (une macro) | `Cenda` ; `Tyalë` | attesté | |
| tourner ; rotation | `Quera` ; `querië` | attesté | |
| miroir | `cilintilla` | attesté | |
| scinder | `Sahta` | néo | |
| joindre, fusionner | `Yanta` | néo ! (Paul Strack) | |
| trier | `Silta` | néo ^ | |
| changer, convertir ; conversion | `Vista` ; `vistië` | attesté | |
| compresser, compression ; recompresser | `Sanga` ; `Ensanga` | MQ ; construit | `nutta` (= virgule) |
| redresser ; redressement | `Térata` ; `tératalë` | néo ! (Helge Fauskanger) | |
| renuméroter | `Ennota` | construit (`en-` + `not-` « compter ») | |
| renommer | `Vista essë` | attesté | |
| réinitialiser, restaurer | `Envinyata` | attesté | |
| mettre à jour ; mise à jour | `Enhanya` ; `enhanyalë` | néo ! (Orondil) | |
| vérifier | `Tyasta` | néo | |
| éditer | `Tengwista` | néo ! (Fëastorno) | |
| imprimer | `Teca` (« écrire ») | MQ — sens étendu | |
| dessiner | `Emba` | néo ! (Arael) | |
| enregistrer (une macro) | `Minateca` | néo ! (Helge Fauskanger) | |
| ajouter | `Napan` | attesté | |
| commencer / terminer | `Yesta` / `Telya` | néo ! / attesté | |
| continuer | `Lelya` (« avancer ») | attesté | |
| ignorer | `Hehta` | attesté | |
| attendre | `Hora` | MQ | |
| arrêter (un traitement) | `Pusta` | MQ | |
| traitement par lot | `úmessë` (« en lot », `úmë` « grand ensemble de choses semblables ») | attesté | `Lotë`, `rimba` seul |
| recevoir ; reçu | `cav-` ; `cavina` | attesté ; participe en `-ina` | `cam-` (déconseillé par Eldamo) |
| avoir, contenir | `sam-` (aoriste `samë`, futur `samuva`) | attesté | |

### États, messages
| Concept | Mot | Statut (source) | À ne pas employer |
|---|---|---|---|
| erreur | `loima` | attesté (« faute ») | `Russë` (= chevelure rousse) |
| avertissement, attention | `órë` | attesté | |
| danger | `raxë` | attesté | |
| sûr | `varna` (pl. `varnë`) | MQ | |
| protéger / protégé | `varya-` / `varyaina` | MQ / construit (participe en `-ina`) | |
| lisible / illisible | `cendima` / `lá cendima` | construit (`cenda-` « lire » + `-ima` « qui peut être ») | |
| invalide | `útéra` (pl. `útérë`) | construit (`ú-` « non » + `téra` « correct », MQ) | |
| énorme / plus grand | `yána` / `ambë höa` | attesté / construit | `haura` (déconseillé par Eldamo) |
| aide, mode d'emploi | `Ásë` | néo ! (Elaran) | |
| info-bulle | `tehta ásëo` (« signe d'aide ») | construit (`tehta` attesté « signe, marque » + `ásë` néo ! Elaran) | |
| terminé, réussi | `carina` | attesté | |
| impossible de… | `Lá cárima` + infinitif | attesté | |
| disponible / indisponible | `férima` / `lá férima` | attesté | |
| invalide | `útéra` | construit (`ú-` + `téra` « correct ») | |
| corrompu | `hastaina` (« abîmé ») | attesté | |
| mémoire (de l'ordinateur) | `rénë` | néo ! (Helge Fauskanger) | |
| pas assez, insuffisant | `ufárea` | MQ | |
| rester (inchangé) | `lemya-` | MQ | |
| morceau, partie | `asta` (pl. `astar`) | attesté (« une de plusieurs parts égales ») | `ranta` (déconseillé, = parcours) |
| réessayer | `á ricë ata` (« essaie de nouveau ») | `ric-` attesté (impératif `á ricë` attesté) + `ata` MQ | |
| oui / non / OK | `Ná` / `Lá` / `Ná` | attesté | `Nai` (= peut-être), `Oia` (= éternel) |
| s'il vous plaît | `Mecin` | attesté | |
| patientez | `Hora` | MQ | |
| nouveau / récent | `vinya` / `telda` (« dernier ») | attesté | |
| vide | `lusta` | MQ | |
| caché | `muina` | MQ | |
| secret, chiffré ; mot de passe | `nulda` ; `nulda quetta` | MQ | |
| inconnu | `ú sinwa` | attesté | |
| automatique | `imcarila` | construit (`im-` « soi-même » + `car-`) | `automáto` |
| manuel | `mánen` (« à la main ») | attesté | |
| licence | `namna` (« statut, loi ») | attesté | |
| gratuit, libre | `lehta` ; `ú telpeo` | attesté | |
| temporaire | `lúmëa` | construit (`lúmë` + `-a`) | |
| original (fichier d'origine) | `celuva` | néo ^ | |
| perdu | `vanwa` | attesté | |
| champ (d'un formulaire) | `nómë` (pl. `nómi`, « lieu ») | attesté — sens étendu | |

### Image, affichage
| Concept | Mot | Statut (source) | À ne pas employer |
|---|---|---|---|
| couleur | `quilë` (pl. `quili`) | néo | |
| luminosité / contraste / saturation | `calimië` / `anquië` / `quilequantië` | construits (`calima`, `anqua` « opposé », `quilë` + `quanta`) | |
| netteté ; adaptative | `maicië` ; `maicië ahyala` | construit (`maica` « aigu ») | |
| niveaux (point noir/blanc) | `tyelli` (« degrés ») ; `morna tikse` / `ninquë tikse` | attesté ; MQ (`tikse` « point ») | |
| seuil | `fenda` | MQ | |
| flou ; tampon de flou | `hísië` (« brume ») ; `tehta hísiëo` | attesté | |
| transparent / transparence | `liquistëa` / `liquistië` | néo | |
| effet | `tyarda` (« résultat ») | néo ! (Tamas Ferencz) | |
| sépia / gris | `varnë` (« brun ») / `sinda` | attesté | |
| profondeur de couleur | `tumnië quilëo` | construit (`tumna` « profond », MQ) | |
| pipette | `quiletamma` | construit | |
| forme, cadre | `canta` (pl. `cantar`) | attesté | |
| rectangle / ellipse / ligne / flèche | `cantil` / `andacorto` / `tië` / `pilin` | néo / construit / attesté / MQ | `renta` |
| poignée | `mahtë` (pl. `mahti`) | néo | |
| clonage | `tatyalë` | construit | |
| texte ; bloc de texte | `sarmë` ; `ronwa` | attesté ; néo ! (Orondil) | |
| marge, bordure | `pelma` (pl. `pelmar`) | attesté | |
| découpe ; net, ferme | `rista` ; `ronda` (`yá i rista ná ronda` « quand la découpe est nette ») | MQ ; attesté | |
| taille / poids / largeur / hauteur / longueur | `höassë` / `lungumë` / `landië` / `tárië` / `andë` | néo / néo / construit / attesté / néo | |
| dimensions | `lestar` | MQ (« mesures ») | |
| résolution | `nótë tikseron` | construit | |
| qualité | `eumë` | néo ! (Luinyelle) | |
| horizontal / vertical / droit | `caimenya` / `tarmenya` / `téra` | néo ! (Luinyelle) / MQ | `thala` |
| angle | `neccë` | attesté | |
| inclinaison | `raicië` | attesté (`raica` « tordu ») | |
| suivant / précédent | `neuna` / `nöa` | MQ / attesté | |
| premier / dernier | `minya` / `métima` | attesté | |
| double | `atatya` | attesté | |
| mode, manière | `lé` | attesté | |
| aperçu | `apacen` | attesté (« prévision ») | |
| molette ; souris | `querma` ; `nyarrincë` | attesté ; néo | |

### Mots-outils
| Concept | Mot | Statut |
|---|---|---|
| et / ou / mais | `ar` / `hya` / `mal` | attesté |
| sans / avec (instrument) / avec (ensemble) | `ú` / suffixe `-nen` / `ó` | attesté |
| dans / sur, au-dessus / sous / entre | `mi` (ou `-ssë`) / `or` / `nu` / `imbë` | attesté |
| autour de | `os` | néo ! (ABNW) |
| vers / depuis | `-nna` / `-llo` (ou `ló`) | attesté |
| par (agent) | `ló` | attesté |
| tous / chaque | `ilyë` / `ilya` | attesté |
| seulement / aussi | `er` / `aryë` (néo), `yú` (MQ) | |
| maintenant / déjà / toujours | `sí` / `tensi` / `illumë` | attesté |
| avant / après / jusqu'à | `nó` / `apa` / `tenna` | attesté |
| si / quand / pendant que / parce que | `qui` / `yá` / `lan` / `pan` (néo) | |
| sauf / donc | `hequa` / `epetai` | attesté |
| à côté de | `ara` | attesté |

## 3. Grammaire à respecter

- **Pas de mutation initiale** : seule la fin du mot varie.
- **Pluriel** : `-r` après voyelle (`emma` → `emmar`, `colca` → `colcar`), `-ë` → `-i` (`cómë` → `cómi`, `essë` → `essi`), `-i` après consonne (`lattin` → `lattini`) ; `-ië` → `-ier`. Adjectif au pluriel : `-a` → `-ë` (`cilina` → `cilinë`), `-ë` → `-i` (`ninquë` → `ninqui`).
- **Cas** : génitif `-o` (pl. `-ron`/`-ion`), possessif `-va`, datif `-n`, allatif `-nna` (pl. `-nnar`), ablatif `-llo` (pl. `-llor`), locatif `-ssë` (pl. `-ssen`), instrumental `-nen` (pl. `-inen`). Les noms propres et sigles (`Windows`, `MosaicView`, `ComicVine`) restent invariables : préposition (`mi`, `na`, `ló`) plutôt que suffixe.
- **Verbes** : impératif de bouton = radical (`Latya`, `Hepa`) ; aoriste pour les messages (`I tamma ná…`) ; passé `-në` ; futur `-uva` ; participe passif `-ina` / `-na` (`carina`, `hepina`) ; but « pour + verbe » = gérondif au datif (`latyien` « pour ouvrir ») ; sujet « vous » = `-lyë` (`merilyë`).
- **Négation** : `lá` (`lá férima`), `ú` « sans » ; interdiction : `Áva`.
- **Mots de deux lettres** (`lé`, `sa`) : le contrôleur ne retrouve pas leurs formes fléchies (racine trop courte). Préférer une tournure avec préposition (`i lé` + génitif, `or sa`) ; `lénen` est accepté tel quel.
