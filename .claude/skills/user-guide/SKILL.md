---
name: user-guide
description: Trouver et modifier le mode d'emploi (fenêtre d'aide "?") de MosaicView — ajouter/modifier une section, son texte, ou son ordre. Utiliser dès qu'une demande porte sur "le mode d'emploi", "la fenêtre d'aide", ou une de leurs sections.
---

# Mode d'emploi (fenêtre d'aide) — MosaicView

## Où c'est

Fichier : `modules/qt/user_guide_qt.py`. Classe principale : `_HelpDialog`. Ouverte via le bouton "?" de la barre latérale (icône d'aide), câblé dans `MosaicView.py::_show_user_guide`.

Le texte affiché n'est **pas** dans `user_guide_qt.py` : il vient de `locales/*.json`, clé racine `"help"`. `user_guide_qt.py` ne contient que la structure (liste des sections, widgets, mise en page), jamais le texte lui-même.

## Comment il est structuré

Liste ordonnée `_HelpDialog._SECTIONS` (vers la ligne 625) : chaque entrée est un tuple `(title_key, content_key)`.

```python
self._SECTIONS = [
    ("",                          "help.intro"),           # intro sans titre (cas spécial)
    ("help.open_files",           "help.open_files_content"),
    ...
    ("help.viewer",               "help.viewer_content"),
    ...
    ("help.shortcuts",            "help.shortcuts_content"),
    ...
    ("help.metadata",             "METADATA_SECTION"),      # section spéciale, voir plus bas
    ...
]
```

- `title_key` : clé de traduction du titre de la section collapsible (ex. `"help.viewer"` → `_("help.viewer")` = "Visionneuse"). Vide `""` uniquement pour l'intro (pas de titre, pas de section collapsible).
- `content_key` : soit une clé de traduction normale (texte simple, affiché via `_SelectableText`), soit un nom en `MAJUSCULES_SECTION` qui déclenche une construction spéciale avec sous-widgets (boutons, liens, aperçus).

L'ordre de la liste = l'ordre d'affichage dans la fenêtre. Pour ajouter une section : insérer un tuple à la position voulue, dans `_SECTIONS`.

## Sections spéciales (`XXX_SECTION`)

Certaines sections ont des widgets au-delà du simple texte (boutons d'action, aperçus, liens cliquables). Repérables par leur `content_key` en majuscules, routées dans la boucle de construction (vers la ligne 667) vers un builder dédié :

| `content_key` | Builder | Contenu |
|---|---|---|
| `METADATA_SECTION` | `_build_metadata_section` | Texte + lien ComicVine scraper |
| `LANGUAGE_SECTION` | `_build_language_section` | Texte + boutons export police pIqaD/Tengwar |
| `CONFIG_SECTION` | `_build_config_section` | Texte + boutons vider fichiers temp/récents/config/presse-papiers |
| `ICONS_SECTION` | `_build_icons_section` | Texte + bouton sauvegarder toutes les icônes |
| `WILHELM_SCREAM_SECTION` | `_build_wilhelm_scream_section` | Texte + bouton export son — voir skill `wilhelm-scream-easter-egg` pour le mécanisme de déclenchement lui-même et l'export |
| `LICENSE_GPL_SECTION`, `LICENSE_UNRAR_SECTION`, `LICENSE_7ZIP_SECTION` | `_build_license_section` | Texte + lien "voir la licence complète" |

Une section normale (texte seul) n'a besoin d'aucun builder : le `else` de la boucle crée directement un `_SelectableText(_(content_key))`.

## Comment modifier le texte d'une section existante

1. Identifier la clé de contenu dans `_SECTIONS` (ex. section "Visionneuse" → `help.viewer_content`).
2. Modifier **uniquement `locales/fr.json`** d'abord (`data["help"]["viewer_content"]`), avec l'outil Edit — jamais de script pour une modif ponctuelle en français.
3. Proposer le texte à l'utilisateur, attendre validation avant d'écrire.
4. Une fois validé et écrit en fr, ne pas enchaîner sur les autres langues sans autorisation explicite — voir skill `add-translation` pour la procédure complète (script Python, une langue à la fois, vigilance tlh/sjn/qya, régénération des 3 variantes CSUR via `conversion_tools/convert_piqad_csur.py` et `convert_tengwar_csur.py`).

Les valeurs `help.*_content` utilisent `\n` pour les retours à la ligne et `•` pour les puces — reprendre ce format existant plutôt que d'improviser un autre style (voir n'importe quelle section existante dans `fr.json` comme modèle).

## Comment ajouter une nouvelle section

1. Choisir une clé de titre (`help.ma_section`) et une clé de contenu (`help.ma_section_content`), les ajouter dans `fr.json` sous `"help"`.
2. Insérer le tuple `("help.ma_section", "help.ma_section_content")` dans `_SECTIONS` à la position voulue.
3. Si la section n'a besoin que de texte : rien d'autre à faire, elle passe par le `else` générique.
4. Si elle a besoin de widgets interactifs (boutons, liens) : écrire un builder `_build_ma_section(self, section)` sur le modèle des builders existants, l'appeler dans la boucle (`elif content_key == "MA_SECTION": sw = self._build_ma_section(section)`), et l'enregistrer aussi dans `_retranslate()` (voir plus bas) pour que la traduction à la volée fonctionne.

## Piège — retraduction à la volée

`_HelpDialog._retranslate()` doit rejouer **toutes** les valeurs affichées (titre de la fenêtre, titres de sections, contenus, textes de boutons des sections spéciales) à chaque changement de langue — c'est une fenêtre non-modale, elle peut rester ouverte pendant que l'utilisateur change de langue (règle générale du projet, voir `CLAUDE.md` règle UI n°2). Si une nouvelle section spéciale est ajoutée avec des sous-widgets, son builder doit stocker les références nécessaires dans `self._section_widgets[title_key]` et `_retranslate()` doit les reparcourir pour réappliquer `setText`/`setFont` — copier le pattern d'une section spéciale existante (ex. `sw["clip_note"].retranslate(...)` pour `CONFIG_SECTION`) plutôt que d'improviser.

## Piège — ne jamais repérer un morceau de texte traduit par son contenu latin

`LANGUAGE_SECTION` découpe `help.language_content` en 3 zones d'affichage distinctes (texte normal / paragraphe des liens de polices avec ses boutons d'export / paragraphe d'attribution en italique). Ce découpage est fait par `_split_language_paragraphs()` (module-level), **par position** : les paragraphes (séparés par `\n\n`) avant celui qui contient `{url_piqad}`/`{url_tengwar}` sont le texte normal, celui des URLs est le paragraphe "polices", tout ce qui suit est l'attribution italique.

**Pourquoi par position et pas par contenu** : chercher un mot littéral (ex. `"Claude" in para`) pour identifier le paragraphe d'attribution ne fonctionnerait pas pour toutes les langues — ça marcherait pour celles qui gardent les noms propres en lettres latines (y compris le klingon pIqaD), mais **pas pour `sjn-tengwar`/`qya-tengwar`, où tout le texte — noms propres compris — est transcrit en glyphes tengwar (plage Unicode privée)**. Un tel paragraphe ne serait alors jamais reclassé ni retraduit et resterait figé dans la langue précédente (texte + police).

**Règle générale à respecter dans tout ce fichier** : les seuls repères fiables dans un texte traduit sont les **placeholders** (`{url_piqad}`, `{scraper}`, `{temp_dir}`...) et les **URLs/noms de fichiers littéraux**, que toutes les traductions conservent en caractères latins (vérifié sur les 46 locales, y compris les 2 tengwar) — jamais un mot ou un nom censé apparaître "tel quel" dans la traduction. Les helpers existants (`_text_with_links_html`, `_text_with_explorer_links_html`, `_text_with_angle_bracket_links_html`) respectent déjà cette règle.
