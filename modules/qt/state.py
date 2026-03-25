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
        self.highlight_rect = None

        # Sélection
        self.selected_indices = set()
        self.selection_rects = {}

        # Sélection par cadre (rubber band)
        self.rubber_band = {
            "active": False,
            "start_x": 0,
            "start_y": 0,
            "rect_id": None
        }


        # Historique Annuler/Refaire
        self.history = []
        self.history_index = -1

        # UI State
        self.loading_label = None
        self.loading_bind_id = None
        self.loading_percent = 0  # Pourcentage de chargement actuel
        self.resizing_label = None
        self.resizing_bind_id = None
        self.resizing_percent = 0
        self.converting = False  # Flag pour bloquer les événements pendant la conversion
        self.converting_label = None
        self.converting_bind_id = None
        self.converting_percent = 0
        self.saving_label = None  # Label de progression de sauvegarde CBZ
        self.saving_percent = 0  # Pourcentage de sauvegarde actuel
        self.print_preparing_label = None
        self.resize_after_id = None
        self.is_rendering = False
        self.last_canvas_width = 0
        self.active_viewers = 0
        self.needs_renumbering = False
        self.renumber_mode = 1  # 1 = auto-détection pages multiples, 2 = énumération simple
        self.modal_open = False  # Flag pour bloquer les menus contextuels
        self.block_canvas_menu = False  # Flag pour bloquer temporairement le menu canvas
        self.block_tooltip = False  # Flag pour bloquer les tooltips (ex: menu déroulant ouvert)
        self.tooltip = None  # Info-bulle pour le taux de compression
        self.dark_mode = False  # Thème sombre activé ou non
        self.is_fullscreen = False  # Mode plein écran activé ou non
        self.empty_canvas_text = None  # Texte d'aide sur le canvas vide (2 lignes)

        # Tri
        self.current_sort_method = None  # Méthode de tri actuelle (None, "name", "type", "weight", etc.)
        self.current_sort_order = "asc"  # Ordre de tri ("asc" ou "desc")

        # Navigation clavier
        self.focused_index = None  # Index de la miniature ayant le focus clavier
        self.focus_rect = None  # Rectangle visuel du focus

        # Répertoire de la première image (pour mode images seules)
        self.first_image_dir = None  # Répertoire d'où provient la première image

        # Navigation dans les répertoires
        self.current_directory = ""  # Répertoire actuel dans la navigation (vide = racine)
        self.all_entries = []  # Toutes les entrées (sans filtre)

        # Maps real_idx ↔ visual_idx (position dans visible_entries)
        # Mise à jour à chaque render_mosaic. Clé absente = élément non visible.
        self.real_to_visual = {}   # real_idx → visual_idx
        self.visual_to_real = {}   # visual_idx → real_idx (None si répertoire virtuel)
        # Liste des tk_img persistants pour les dossiers virtuels (anti-GC)
        self._dir_tk_imgs = []
        # visual_idx du dossier virtuel actuellement "sélectionné" (cadre bleu), ou None
        self.selected_dir_visual_idx = None
        self.selected_dir_rect = None  # id du rectangle canvas

        # Compteur de fusions
        self.merge_counter = 0  # Nombre de comics fusionnés (pour les préfixes NEW01-, NEW02-, etc.)

# Instance globale de l'état (sera créée après l'initialisation de root)
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
