# -------------------------
# Classe d'état de l'application
# -------------------------
class AppState:
    """Encapsule toutes les variables d'état de l'application"""
    def __init__(self):
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

        # Sélection
        self.selected_indices = set()

        # Historique Annuler/Refaire
        self.history = []
        self.history_index = -1

        # Valeur de netteté commitée par la visionneuse (outil "sharpness" de
        # la barre d'outils flottante, idees.txt #3), indexée par
        # history_index APRÈS le commit — clé = (image_idx, history_index),
        # valeur = int. Sur state (pas sur ImageViewer) pour survivre à une
        # fermeture/réouverture de la visionneuse : l'historique undo/redo
        # lui-même (history/history_index ci-dessus) vit tant que le fichier
        # reste ouvert, indépendamment des fenêtres de visionneuse ouvertes
        # dessus — cette correspondance doit suivre la même durée de vie pour
        # qu'un undo/redo qui retombe sur ce point après une réouverture
        # affiche encore la bonne valeur sur le slider/spinbox. Voir
        # modules/qt/adjustments_tool_qt.py::AdjustmentsViewerMixin.
        self.sharpness_value_by_history_index: dict[tuple[int, int], int] = {}

        # Réglages de netteté adaptative (Unsharp Mask) commités par la
        # visionneuse (outil "sharpness" en mode unsharp de la barre d'outils
        # flottante, idees.txt #3), indexés par history_index APRÈS le commit
        # — même principe et même durée de vie que sharpness_value_by_
        # history_index ci-dessus, mais un tuple (radius, percent, threshold)
        # au lieu d'un int puisque l'Unsharp Mask a 3 réglettes indépendantes.
        # Voir modules/qt/adjustments_tool_qt.py::AdjustmentsViewerMixin.
        self.unsharp_value_by_history_index: dict[tuple[int, int], tuple[float, int, int]] = {}

        # Réglages de luminosité/contraste commités par la visionneuse (outil
        # "brightness" de la barre d'outils flottante, idees.txt #3), indexés
        # par history_index APRÈS le commit — même principe et même durée de
        # vie que sharpness_value_by_history_index ci-dessus, mais un tuple
        # (brightness, contrast) au lieu d'un int puisque ce mode a 2
        # réglettes indépendantes dans un seul panneau (pas de bi-mode,
        # contrairement à sharpness/unsharp). Voir
        # modules/qt/brightness_tool_qt.py::BrightnessViewerMixin.
        self.brightness_value_by_history_index: dict[tuple[int, int], tuple[int, int]] = {}

        # Valeur de saturation commitée par la visionneuse (outil "saturation"
        # de la barre d'outils flottante, idees.txt #3), indexée par
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
        # (outil "remove_colors" de la barre d'outils flottante, idees.txt
        # #3), indexée par history_index APRÈS le commit — même principe et
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

        # UI State
        self.converting = False  # Flag pour bloquer les événements pendant la conversion
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
        self.all_entries = []  # Toutes les entrées (sans filtre)

        # Maps real_idx ↔ visual_idx (position dans visible_entries)
        # Mise à jour à chaque render_mosaic. Clé absente = élément non visible.
        self.real_to_visual = {}   # real_idx → visual_idx
        self.visual_to_real = {}   # visual_idx → real_idx (None si répertoire virtuel)
        # visual_idx du dossier virtuel actuellement "sélectionné" (cadre bleu), ou None
        self.selected_dir_visual_idx = None

        # Compteur de fusions
        self.merge_counter = 0  # Nombre de comics fusionnés (pour les préfixes NEW01-, NEW02-, etc.)

# Instance globale de l'état (créée à l'initialisation de PanelWidget)
state = None

# Liste globale des dialogues actifs (pour mise à jour de la langue à la volée)
active_dialogs = []

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
