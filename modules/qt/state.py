from collections.abc import MutableSet


class PageSelection(MutableSet):
    """Pages sélectionnées d'un document (AppState.selected_indices).

    Vue de l'extérieur, un ensemble de positions dans images_data, comme un
    set : `idx in sel`, parcours (positions croissantes), len, add, discard,
    clear, et remplacement par affectation (AppState.selected_indices = {...}).
    En interne, rangée par jeton de page (undo_redo.entry_key) : la sélection
    suit ses pages quand d'autres pages sont insérées, supprimées ou déplacées
    (une page convertie insérée avant elle, une page arrivée par l'import web
    ou une numérisation pendant une opération…), et une page retirée du
    document n'y figure plus. Rangée par position, la sélection désignait
    alors d'autres pages, auxquelles s'appliquait la commande suivante.

    Toute page passée par position doit être dans images_data au moment de
    l'appel : une position hors limites est ignorée."""

    def __init__(self, state):
        self._state = state
        self._keys = set()

    @classmethod
    def _from_iterable(cls, it):
        # Résultat des opérations d'ensemble (sel - x, x - sel, sel | x…) :
        # un set de positions ordinaire, détaché du document.
        return set(it)

    def _key_at(self, idx):
        data = self._state.images_data
        if isinstance(idx, int) and 0 <= idx < len(data):
            from modules.qt.undo_redo import entry_key
            return entry_key(data[idx])
        return None

    def _positions(self):
        if not self._keys:
            return []
        from modules.qt.undo_redo import entry_key
        return [i for i, e in enumerate(self._state.images_data) if entry_key(e) in self._keys]

    def __contains__(self, idx):
        key = self._key_at(idx)
        return key is not None and key in self._keys

    def __iter__(self):
        return iter(self._positions())

    def __len__(self):
        return len(self._positions())

    def __bool__(self):
        # Testé très souvent (menus, colonne d'icônes) : sans jeton retenu,
        # inutile de parcourir les pages.
        if not self._keys:
            return False
        from modules.qt.undo_redo import entry_key
        return any(entry_key(e) in self._keys for e in self._state.images_data)

    def add(self, idx):
        key = self._key_at(idx)
        if key is not None:
            self._keys.add(key)

    def discard(self, idx):
        key = self._key_at(idx)
        if key is not None:
            self._keys.discard(key)

    def clear(self):
        self._keys.clear()

    def replace(self, indices):
        """Remplace toute la sélection par les pages aux positions `indices`."""
        keys = {self._key_at(i) for i in indices}
        keys.discard(None)
        self._keys = keys

    def __repr__(self):
        return f"PageSelection({sorted(self)})"


# -------------------------
# Classe d'état de l'application
# -------------------------
class AppState:
    """Encapsule toutes les variables d'état de l'application"""
    def __init__(self):
        # Numéro du panneau (1 ou 2), posé par PanelWidget : cité dans les
        # dernières actions du journal des erreurs (error_log.record_action)
        self.panel_number = None
        # Données de l'archive
        self.images_data = []
        self.current_file = None
        self.modified = False
        self.comic_metadata = None  # Métadonnées ComicInfo.xml
        self.original_page_count = None  # Nombre de pages initial dans les métadonnées

        # Affichage
        self.thumb_w, self.thumb_h = 150, 200
        self.padding_x, self.padding_y = 5, 5
        self.current_thumb_size = 1

        # Drag & Drop
        self.dragging = {
            "idx": None,
            "drag_img": None,
            "offset_x": 0,
            "offset_y": 0,
            "start_x": 0,
            "start_y": 0,
            "moved": False
        }

        # Sélection : positions vues de l'extérieur, pages en interne
        # (PageSelection, propriété selected_indices ci-dessous)
        self._selection = PageSelection(self)

        # Historique Annuler/Refaire
        self.history = []
        self.history_index = -1

        # Valeur de netteté commitée par la visionneuse (outil "sharpness" de
        # la barre d'outils flottante), indexée par le point d'historique
        # APRÈS le commit — clé = tool_value_key(idx) : (jeton de la page,
        # repère du point d'historique), valeur = int. Sur state (pas sur ImageViewer) pour survivre à une
        # fermeture/réouverture de la visionneuse : l'historique undo/redo
        # lui-même (history/history_index ci-dessus) vit tant que le fichier
        # reste ouvert, indépendamment des fenêtres de visionneuse ouvertes
        # dessus — cette correspondance doit suivre la même durée de vie pour
        # qu'un undo/redo qui retombe sur ce point après une réouverture
        # affiche encore la bonne valeur sur le slider/spinbox. Voir
        # modules/qt/sharpness_tool_qt.py::SharpnessViewerMixin.
        self.sharpness_value_by_history_index: dict[tuple[int, int], int] = {}

        # Réglages de netteté adaptative (Unsharp Mask) commités par la
        # visionneuse (outil "sharpness" en mode unsharp de la barre d'outils
        # flottante), indexés par history_index APRÈS le commit
        # — même principe et même durée de vie que sharpness_value_by_
        # history_index ci-dessus, mais un tuple (radius, percent, threshold)
        # au lieu d'un int puisque l'Unsharp Mask a 3 réglettes indépendantes.
        # Voir modules/qt/sharpness_tool_qt.py::SharpnessViewerMixin.
        self.unsharp_value_by_history_index: dict[tuple[int, int], tuple[float, int, int]] = {}

        # Réglages de luminosité/contraste commités par la visionneuse (outil
        # "brightness" de la barre d'outils flottante), indexés
        # par history_index APRÈS le commit — même principe et même durée de
        # vie que sharpness_value_by_history_index ci-dessus, mais un tuple
        # (brightness, contrast) au lieu d'un int puisque ce mode a 2
        # réglettes indépendantes dans un seul panneau (pas de bi-mode,
        # contrairement à sharpness/unsharp). Voir
        # modules/qt/brightness_tool_qt.py::BrightnessViewerMixin.
        self.brightness_value_by_history_index: dict[tuple[int, int], tuple[int, int]] = {}

        # Valeur de saturation commitée par la visionneuse (outil "saturation"
        # de la barre d'outils flottante), indexée par
        # history_index APRÈS le commit — même principe et même durée de vie
        # que sharpness_value_by_history_index ci-dessus. Contrairement à
        # sharpness, jamais relue pour resynchroniser le slider (voir
        # modules/qt/saturation_tool_qt.py::_reset_saturation_preview) : le
        # slider revient toujours à 0 après un commit de saturation, ce dict
        # sert uniquement de trace pour un éventuel usage futur (undo/redo ne
        # s'appuie pas dessus pour cet outil, contrairement à sharpness/
        # brightness). Voir modules/qt/saturation_tool_qt.py::SaturationViewerMixin.
        self.saturation_value_by_history_index: dict[tuple[int, int], int] = {}

        # Valeur de suppression des couleurs commitée par la visionneuse
        # (outil "remove_colors" de la barre d'outils flottante),
        # indexée par history_index APRÈS le commit — même principe et
        # même durée de vie que sharpness_value_by_history_index ci-dessus.
        # Contrairement à sharpness, jamais relue pour resynchroniser le
        # slider (voir modules/qt/remove_colors_tool_qt.py::
        # _reset_remove_colors_preview) : le slider revient toujours à 0
        # après un commit, ce dict sert uniquement de trace pour un éventuel
        # usage futur (undo/redo ne s'appuie pas dessus pour cet outil,
        # contrairement à sharpness/brightness) — même principe que
        # saturation_value_by_history_index. Voir
        # modules/qt/remove_colors_tool_qt.py::RemoveColorsViewerMixin.
        self.remove_colors_value_by_history_index: dict[tuple[int, int], int] = {}

        # Qualité JPEG cible commitée par la visionneuse (outil "compression"
        # de la barre d'outils flottante), indexée par
        # history_index APRÈS le commit — même principe et même durée de vie
        # que sharpness_value_by_history_index ci-dessus. Contrairement à
        # saturation/remove_colors, RELUE pour resynchroniser le slider (comme
        # sharpness/brightness) : le slider ne revient PAS à une valeur fixe
        # après un commit, mais à la qualité JPEG RÉELLE de l'image après
        # recompression (detect_jpeg_quality(entry['bytes']), voir
        # modules/qt/compression_tool_qt.py::_reset_compression_preview) — ce
        # dict mémorise cette valeur détectée pour ne pas avoir à rouvrir/
        # redécoder les bytes à chaque resynchronisation.
        self.compression_value_by_history_index: dict[tuple[int, int], int] = {}

        # Réglages de niveaux noir/blanc commités par la visionneuse (outil
        # "levels" de la barre d'outils flottante), indexés par
        # history_index APRÈS le commit — même principe et même durée de vie
        # que sharpness_value_by_history_index ci-dessus, mais un tuple
        # (threshold, black_point, gamma, white_point) puisque ce mode a 4
        # contrôles indépendants (2 sliders + gamma + seuil, plus 2 pipettes
        # qui écrivent dans black_point/white_point sans valeur propre). RELUE
        # pour resynchroniser les 4 contrôles (comme sharpness/brightness/
        # compression) : ils restent sur la dernière valeur commitée, pas une
        # valeur fixe. Voir modules/qt/levels_tool_qt.py::LevelsViewerMixin.
        self.levels_value_by_history_index: dict[tuple[int, int], tuple[int, int, float, int]] = {}

        # Snapshot "avant premier changement" par page pour l'outil
        # "color_depth" de la barre d'outils flottante — clé = page_key(idx)
        # (jeton de la page, jamais sa position : après une suppression ou un
        # déplacement, la position désigne une autre page, qui recevrait les
        # bytes d'origine de la première), valeur = bytes d'origine. Contrairement aux dicts
        # *_value_by_history_index ci-dessus (indexés par (page, point
        # d'historique), RESYNCHRONISÉS à chaque changement de page/undo-redo),
        # celui-ci n'est PAS dérivé de l'historique : il doit survivre à un
        # changement de page ET à un Ctrl+Z/Ctrl+Y pendant que l'outil est
        # actif (sinon risque de confusion pour l'utilisateur). Capturé au
        # premier clic sur une profondeur pour
        # une page donnée, jamais écrasé tant qu'il existe (un enchaînement
        # 32->24->8 bits garde le TOUT premier snapshot) ; retiré uniquement
        # quand "Restaurer l'original" est cliqué pour cette page. Sur state
        # (pas sur ImageViewer) pour la même raison que les dicts ci-dessus :
        # survivre à une fermeture/réouverture de la visionneuse tant que le
        # fichier reste ouvert. Voir modules/qt/color_depth_tool_qt.py.
        self.color_depth_original_bytes_by_page: dict[int, bytes] = {}

        # Même mécanisme que color_depth_original_bytes_by_page ci-dessus,
        # pour l'outil "effects" de la barre d'outils flottante — clé =
        # page_key(idx), valeur = bytes d'origine avant
        # le tout premier changement d'effet de la session. Survit au
        # changement de page ET à un Ctrl+Z/Ctrl+Y, retiré uniquement par
        # "Restaurer l'original". Voir modules/qt/effects_tool_qt.py.
        self.effect_original_bytes_by_page: dict[int, bytes] = {}

        # Écart par rapport à color_depth : le dernier effet appliqué
        # (grayscale/sepia/invert) ne laisse aucune trace détectable dans le
        # mode PIL de l'image (contrairement à la profondeur de couleur), donc
        # ne peut pas être retrouvé après coup en inspectant l'image — mémorisé
        # explicitement ici. Clé = page_key(idx), valeur = clé d'effet ('none' si
        # absent). Retiré (pop) en même temps que l'entrée ci-dessus quand
        # "Restaurer l'original" est cliqué. Voir modules/qt/effects_tool_qt.py.
        self.effect_key_by_page: dict[int, str] = {}

        # Même mécanisme que color_depth_original_bytes_by_page ci-dessus,
        # pour l'outil "image_mode" de la barre d'outils flottante — clé =
        # page_key(idx), valeur = bytes d'origine avant le tout
        # premier changement de mode de la session. Survit au changement de
        # page ET à un Ctrl+Z/Ctrl+Y, retiré uniquement par "Restaurer
        # l'original". Contrairement à effect_key_by_page, pas besoin d'un
        # dict séparé pour le radio verrouillé : le mode réel de l'image le
        # fait foi (comme color_depth), voir modules/qt/image_mode_tool_qt.py.
        self.image_mode_original_bytes_by_page: dict[int, bytes] = {}

        # UI State
        self.saving_label = None  # Label de progression de sauvegarde CBZ
        self.saving_percent = 0  # Pourcentage de sauvegarde actuel
        self.is_rendering = False
        self.last_canvas_width = 0
        self.active_viewers = 0
        self.needs_renumbering = False
        self.renumber_mode = 1  # 0 = OFF (désactivé), 1 = auto-détection pages multiples, 2 = énumération simple
        self.zip_compression_state = None  # 'stored' | 'deflated' | None (non-CBZ ou aucun fichier) — détecté à l'ouverture
        self.modal_open = False  # Flag pour bloquer les menus contextuels
        self.block_canvas_menu = False  # Flag pour bloquer temporairement le menu canvas
        self.block_tooltip = False  # Flag pour bloquer les tooltips (ex: menu déroulant ouvert)
        self.tooltip = None  # Info-bulle pour le taux de compression
        self.dark_mode = False  # Thème sombre activé ou non
        self.is_fullscreen = False  # Mode plein écran activé ou non

        # Tri
        self.current_sort_method = None  # Méthode de tri actuelle (None, "name", "type", "weight", etc.)
        self.current_sort_order = "asc"  # Ordre de tri ("asc" ou "desc")

        # Navigation clavier
        self.focused_index = None  # Index de la miniature ayant le focus clavier

        # Répertoire de la première image (pour mode images seules)
        self.first_image_dir = None  # Répertoire d'où provient la première image

        # Navigation dans les répertoires
        self.current_directory = ""  # Répertoire actuel dans la navigation (vide = racine)

        # Maps real_idx ↔ visual_idx (position dans visible_entries)
        # Mise à jour à chaque render_mosaic. Clé absente = élément non visible.
        self.real_to_visual = {}   # real_idx → visual_idx
        self.visual_to_real = {}   # visual_idx → real_idx (None si répertoire virtuel)
        # visual_idx du dossier virtuel actuellement "sélectionné" (cadre bleu), ou None
        self.selected_dir_visual_idx = None

        # Compteur de fusions
        self.merge_counter = 0  # Nombre de comics fusionnés (pour les préfixes NEW01-, NEW02-, etc.)

        # Numéro du document ouvert dans ce panneau : incrémenté à chaque
        # fermeture (force_close_file) et à chaque chargement qui remplace tout
        # le contenu (archive, PDF). Une opération asynchrone le capture au
        # démarrage et abandonne son résultat s'il a changé à la fin : le
        # document qu'elle visait a été fermé ou remplacé entre-temps.
        self.doc_generation = 0

        # État sur le disque de current_file (utils.file_signature) quand ce
        # panneau l'a lu ou écrit pour la dernière fois, None si inconnu.
        # Comparé juste avant de réécrire current_file : s'il a changé, le
        # fichier a été modifié ailleurs (autre panneau, Bibliothèque, autre
        # programme) et l'enregistrement demande confirmation (skill save-export).
        self.disk_signature = None

        # Opérations en arrière-plan qui modifient les pages de ce document
        # (rotation, miroir, redressement automatique, redimensionnement,
        # conversion de format). Tant qu'il en reste une, Annuler/Refaire est
        # sans effet : il restaurerait des pages que le worker continue de
        # modifier, et le save_state de fin d'opération effacerait la branche
        # Refaire (voir begin_operation/end_operation, undo_redo_qt).
        self.running_operations = []

        # Ajouts de pages arrivés pendant une opération (numérisation, import
        # web, fusion, métadonnées ComicVine…) : (doc_generation, fn), appliqués
        # par end_operation quand la dernière opération se termine
        # (voir run_when_idle).
        self.pending_when_idle = []

    @property
    def selected_indices(self) -> PageSelection:
        """Pages sélectionnées, par position (voir PageSelection)."""
        return self._selection

    @selected_indices.setter
    def selected_indices(self, indices):
        if indices is self._selection:
            return
        self._selection.replace(list(indices))

    def begin_operation(self, token):
        """Inscrit une opération en arrière-plan sur ce document (token : son
        worker). À retirer par end_operation sur TOUS ses chemins de fin."""
        from modules.qt.error_log import record_action
        record_action("operation_start", self.panel_number, name=type(token).__name__)
        self.running_operations.append(token)

    def end_operation(self, token):
        if any(t is token for t in self.running_operations):
            from modules.qt.error_log import record_action
            record_action("operation_end", self.panel_number, name=type(token).__name__)
        self.running_operations = [t for t in self.running_operations if t is not token]
        if self.running_operations or not self.pending_when_idle:
            return
        pending, self.pending_when_idle = self.pending_when_idle, []
        for generation, fn in pending:
            if generation == self.doc_generation:
                # Un ajout en échec n'empêche jamais les suivants (une page
                # numérisée ne doit pas être perdue parce que l'ajout d'avant
                # a échoué), ni la fin de l'opération qui appelle
                # end_operation (l'écriture d'un enregistrement réussie passerait
                # pour un échec).
                try:
                    fn()
                except Exception as e:
                    from modules.qt.error_log import log_exception
                    log_exception(e, "error_log.entry.caught")

    def operation_running(self) -> bool:
        return bool(self.running_operations)

    def document_is_empty(self) -> bool:
        """Un fichier est ouvert mais le document ne contient plus aucun
        fichier : ni page, ni fichier annexe (ComicInfo.xml compris, rangé dans
        images_data comme les autres ; comic_metadata n'en est qu'une copie
        analysée, qui peut rester en retard). Critère unique du texte « BD
        vide » de la mosaïque et de la question avant d'enregistrer : toute
        autre donnée écrite dans le CBZ qui viendrait à être conservée dans
        l'état (commentaire d'archive…) doit être prise en compte ici."""
        return self.current_file is not None and not self.images_data

    def run_when_idle(self, fn):
        """Exécute fn() maintenant, ou à la fin de l'opération en cours sur ce
        document (end_operation) si une opération tourne.

        Pour tout ce qui modifie le document en arrivant de façon asynchrone
        (fin d'un téléchargement, d'une numérisation, d'une fusion…) : appliqué
        pendant une opération, l'ajout serait effacé par son annulation (retour
        au sommet de l'historique pris avant elle), ou l'annulation remettrait
        `modified` à sa valeur d'avant l'opération. Abandonné si le document a
        été fermé ou remplacé entre-temps (doc_generation)."""
        if not self.running_operations:
            fn()
            return
        self.pending_when_idle.append((self.doc_generation, fn))

    def page_key(self, idx):
        """Clé durable de la page à la position `idx` (None hors limites) :
        le jeton de l'historique (undo_redo.entry_key), qui suit la page quand
        elle change de position et qu'une restauration par Annuler reprend.
        Jamais la position elle-même pour mémoriser quelque chose d'une page :
        après une suppression, un déplacement ou une insertion, elle désigne
        une autre page."""
        if not 0 <= idx < len(self.images_data):
            return None
        from modules.qt.undo_redo import entry_key
        return entry_key(self.images_data[idx])

    def tool_value_key(self, idx):
        """Clé des dicts *_value_by_history_index : (jeton de la page à la
        position `idx`, repère du point d'historique courant). Jamais
        (position, history_index) : la position désigne une autre page après
        une suppression ou un déplacement, et history_index un autre état
        après une saturation de l'historique ou une nouvelle action faite
        après des Annuler — la réglette afficherait alors la valeur d'une
        autre page ou d'un autre état."""
        from modules.qt.undo_redo import history_point_key
        return (self.page_key(idx), history_point_key(self))

    def reset_per_file_tool_memory(self):
        """Purge la mémoire des outils de la visionneuse propre au fichier
        ouvert : indexée par position de page et/ou par history_index, ou par
        page_key pour les originaux de "Restaurer l'original" (profondeur de
        couleur, effets, mode d'image), elle n'a plus de sens une fois le
        fichier fermé (l'historique est remis à zéro et la page N d'un autre
        fichier n'a rien à voir), et ces originaux occuperaient la mémoire.
        crop_mask_px n'est pas concerné : exprimé en pixels absolus et non
        lié à une page précise, le masque de découpe mémorisé reste
        applicable d'un fichier à l'autre."""
        self.sharpness_value_by_history_index.clear()
        self.unsharp_value_by_history_index.clear()
        self.brightness_value_by_history_index.clear()
        self.saturation_value_by_history_index.clear()
        self.remove_colors_value_by_history_index.clear()
        self.compression_value_by_history_index.clear()
        self.levels_value_by_history_index.clear()
        self.color_depth_original_bytes_by_page.clear()
        self.effect_original_bytes_by_page.clear()
        self.effect_key_by_page.clear()
        self.image_mode_original_bytes_by_page.clear()


# Instance globale de l'état (créée à l'initialisation de PanelWidget)
state = None


def resolve_state_after_wrapped_call(prev, panel_state, active_before, active_after,
                                     valid_states):
    """Valeur à redonner au singleton `state` à la fin d'un callback enveloppé
    par PanelWidget._build_menubar_callbacks (qui l'a pointé sur panel_state le
    temps de l'appel).

    - Si le callback a changé de panneau actif (ex. fermeture du split depuis
      le menu du panneau 2 : le panneau 1 redevient actif), le singleton doit
      suivre le nouveau panneau actif. Restaurer `prev` le laisserait sur le
      panneau 2 masqué, désynchronisé de MainWindow._active_panel — et
      MainWindow.eventFilter ne le recale que quand le panneau actif change.
    - Sinon, restaurer `prev` s'il appartient encore à un panneau existant,
      à défaut panel_state.

    active_before/active_after : AppState du panneau actif avant/après l'appel
    (None si inconnu). valid_states : AppState de tous les panneaux existants."""
    if active_after is not None and active_after is not active_before:
        return active_after
    if any(prev is s for s in valid_states):
        return prev
    return panel_state

# Constantes pour les fichiers récents
MAX_RECENT_FILES = 10

# Constantes pour les limites de taille de police
MIN_FONT_SIZE_OFFSET = -5  # Permet de réduire la police de 5 points maximum
MAX_FONT_SIZE_OFFSET = 10  # Permet d'augmenter la police de 10 points maximum

# Définition des thèmes
THEMES = {
    "light": {
        "bg": "#f5f5f5",          # Fond clair pour canvas et main_frame
        "canvas_bg": "#f5f5f5",   # Fond du canvas/onglets
        "toolbar_bg": "#e0e0e0",  # Fond du bandeau de boutons
        "separator": "#808080",   # Séparateur
        "text": "#000000",        # Texte
        "disabled": "#999999",    # Texte désactivé
        "entry_bg": "#ffffff",    # Fond des champs de saisie
        "link": "#0066cc",        # Couleur des liens hypertextes
        "tooltip_bg": "#ffffe0",  # Fond des info-bulles
        "tooltip_fg": "#000000",  # Texte des info-bulles
        "icon_hover": "#cccccc",  # Fond survol icônes toolbar
    },
    "dark": {
        "bg": "#2b2b2b",          # Fond sombre pour canvas et main_frame
        "canvas_bg": "#2b2b2b",   # Fond du canvas/onglets
        "toolbar_bg": "#1e1e1e",  # Fond du bandeau de boutons
        "separator": "#555555",   # Séparateur
        "text": "#ffffff",        # Texte
        "disabled": "#aaaaaa",    # Texte désactivé
        "entry_bg": "#3c3c3c",    # Fond des champs de saisie
        "link": "#66b3ff",        # Couleur des liens hypertextes (bleu clair pour mode sombre)
        "tooltip_bg": "#3c3c3c",  # Fond des info-bulles
        "tooltip_fg": "#ffffff",  # Texte des info-bulles
        "icon_hover": "#4a4a4a",  # Fond survol icônes toolbar
    }
}

def get_current_theme():
    """Retourne le thème actuel (clair ou sombre) selon state.dark_mode"""
    return THEMES["dark"] if state.dark_mode else THEMES["light"]
