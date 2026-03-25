"""
Module de gestion centralisée de la configuration pour MosaicView
Gère tous les paramètres de l'application dans un fichier JSON unique
"""

import json
import os
import sys
import tempfile


class ConfigManager:
    """Gestionnaire centralisé de configuration pour l'application"""

    # Nom du fichier de configuration
    CONFIG_FILENAME = ".mosaicview_config.json"

    # Valeurs par défaut pour tous les paramètres
    DEFAULT_CONFIG = {
        'language': None,  # None = détection automatique
        'sidebar_collapsed': True,   # Barre d'icônes rabattue par défaut
        'sidebar_collapsed_panel2': True,  # Barre d'icônes panel2 rabattue par défaut
        'fullscreen': False,  # Mode fenêtré par défaut
        'maximized': False,  # Fenêtre maximisée par défaut (False)
        'window_position': None,  # None = centré par défaut
        'window_size': {'width': 1240, 'height': 780},  # Taille par défaut
        'dark_mode': False,  # Mode clair par défaut
        'thumbnail_size': 'normal',  # Taille normale par défaut ('small', 'normal', 'large')
        'font_size_offset': 0,  # Offset additif pour la taille de police (0 = taille par défaut)
        'buttons_column_width': 220,  # Largeur de la colonne de boutons (par défaut 220px)
        'recent_files': [],  # Liste des fichiers récemment ouverts (max 10)
        'use_icon_toolbar': False,  # TEMPORAIRE (dev) — barre d'icônes active
    }

    def __init__(self, config_dir=None):
        """
        Initialise le gestionnaire de configuration

        Args:
            config_dir: Répertoire où est stocké le fichier de configuration (optionnel)
                       Si None, utilise le répertoire MosaicViewTemp
        """
        if config_dir is None:
            # Utilise le répertoire temporaire MosaicViewTemp
            temp_base = tempfile.gettempdir()
            config_dir = os.path.join(temp_base, "MosaicViewTemp")

            # Crée le répertoire s'il n'existe pas
            if not os.path.exists(config_dir):
                os.makedirs(config_dir, exist_ok=True)

        self.config_dir = config_dir
        self.config_file = os.path.join(self.config_dir, self.CONFIG_FILENAME)
        self.config = self.DEFAULT_CONFIG.copy()

        # Charge la configuration existante
        config_loaded = self.load_config()

        # Sauvegarde toujours pour :
        # - Créer le fichier s'il n'existe pas
        # - Mettre à jour avec les nouvelles clés si la config a été chargée
        self.save_config()

    def load_config(self):
        """
        Charge la configuration depuis le fichier JSON

        Returns:
            True si le chargement a réussi, False sinon
        """
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    loaded_config = json.load(f)
                    # Merge avec les valeurs par défaut pour gérer les nouvelles clés
                    self.config = {**self.DEFAULT_CONFIG, **loaded_config}
                    return True
        except Exception as e:
            print(f"Erreur lors du chargement de la configuration : {e}")
        return False

    def save_config(self):
        """
        Sauvegarde la configuration dans le fichier JSON

        Returns:
            True si la sauvegarde a réussi, False sinon
        """
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"Erreur lors de la sauvegarde de la configuration : {e}")
            return False

    # ===== Getters =====

    def get(self, key, default=None):
        """
        Récupère une valeur de configuration

        Args:
            key: Clé de configuration
            default: Valeur par défaut si la clé n'existe pas

        Returns:
            Valeur de configuration ou default si non trouvée
        """
        return self.config.get(key, default)

    def get_language(self):
        """Récupère la langue configurée"""
        return self.config.get('language')

    def get_sidebar_collapsed(self):
        """Récupère l'état de la barre de boutons (True = rabattue, False = déployée)"""
        return self.config.get('sidebar_collapsed', self.DEFAULT_CONFIG['sidebar_collapsed'])

    def get_fullscreen(self):
        """Récupère l'état du mode plein écran"""
        return self.config.get('fullscreen', self.DEFAULT_CONFIG['fullscreen'])

    def get_maximized(self):
        """Récupère l'état maximisé de la fenêtre"""
        return self.config.get('maximized', self.DEFAULT_CONFIG['maximized'])

    def get_window_position(self):
        """
        Récupère la position de la fenêtre

        Returns:
            Dict {'x': int, 'y': int} ou None si pas de position sauvegardée
        """
        return self.config.get('window_position')

    def get_window_size(self):
        """
        Récupère la taille de la fenêtre

        Returns:
            Dict {'width': int, 'height': int}
        """
        return self.config.get('window_size', self.DEFAULT_CONFIG['window_size'])

    def get_dark_mode(self):
        """Récupère l'état du mode sombre"""
        return self.config.get('dark_mode', self.DEFAULT_CONFIG['dark_mode'])

    def get_thumbnail_size(self):
        """
        Récupère la taille des vignettes

        Returns:
            'small', 'normal' ou 'large'
        """
        return self.config.get('thumbnail_size', self.DEFAULT_CONFIG['thumbnail_size'])

    def get_font_size_offset(self):
        """
        Récupère l'offset de taille de police

        Returns:
            Offset entier (0 = taille par défaut)
        """
        return self.config.get('font_size_offset', self.DEFAULT_CONFIG['font_size_offset'])

    def get_buttons_column_width(self):
        """
        Récupère la largeur de la colonne de boutons

        Returns:
            Largeur en pixels (défaut: 220)
        """
        return self.config.get('buttons_column_width', self.DEFAULT_CONFIG['buttons_column_width'])

    # ===== Setters =====

    def set(self, key, value, save=True):
        """
        Définit une valeur de configuration

        Args:
            key: Clé de configuration
            value: Nouvelle valeur
            save: Si True, sauvegarde immédiatement dans le fichier (défaut: True)

        Returns:
            True si la sauvegarde a réussi (si save=True), False sinon
        """
        self.config[key] = value
        if save:
            return self.save_config()
        return True

    def set_language(self, language, save=True):
        """
        Définit la langue de l'application

        Args:
            language: Code de langue (ex: 'fr', 'en')
            save: Si True, sauvegarde immédiatement

        Returns:
            True si la sauvegarde a réussi
        """
        return self.set('language', language, save)

    def set_sidebar_collapsed(self, collapsed, save=True):
        """
        Définit l'état de la barre de boutons

        Args:
            collapsed: True si rabattue, False si déployée
            save: Si True, sauvegarde immédiatement

        Returns:
            True si la sauvegarde a réussi
        """
        return self.set('sidebar_collapsed', collapsed, save)

    def get_sidebar_collapsed_panel2(self):
        return self.config.get('sidebar_collapsed_panel2', self.DEFAULT_CONFIG['sidebar_collapsed_panel2'])

    def set_sidebar_collapsed_panel2(self, collapsed, save=True):
        return self.set('sidebar_collapsed_panel2', collapsed, save)

    def set_fullscreen(self, fullscreen, save=True):
        """
        Définit l'état du mode plein écran

        Args:
            fullscreen: True si plein écran, False si fenêtré
            save: Si True, sauvegarde immédiatement

        Returns:
            True si la sauvegarde a réussi
        """
        return self.set('fullscreen', fullscreen, save)

    def set_maximized(self, maximized, save=True):
        """
        Définit l'état maximisé de la fenêtre

        Args:
            maximized: True si maximisé, False sinon
            save: Si True, sauvegarde immédiatement

        Returns:
            True si la sauvegarde a réussi
        """
        return self.set('maximized', maximized, save)

    def set_window_position(self, x, y, save=True):
        """
        Définit la position de la fenêtre

        Args:
            x: Position X
            y: Position Y
            save: Si True, sauvegarde immédiatement

        Returns:
            True si la sauvegarde a réussi
        """
        return self.set('window_position', {'x': x, 'y': y}, save)

    def set_window_size(self, width, height, save=True):
        """
        Définit la taille de la fenêtre

        Args:
            width: Largeur
            height: Hauteur
            save: Si True, sauvegarde immédiatement

        Returns:
            True si la sauvegarde a réussi
        """
        return self.set('window_size', {'width': width, 'height': height}, save)

    def set_dark_mode(self, dark_mode, save=True):
        """
        Définit l'état du mode sombre

        Args:
            dark_mode: True si mode sombre, False si mode clair
            save: Si True, sauvegarde immédiatement

        Returns:
            True si la sauvegarde a réussi
        """
        return self.set('dark_mode', dark_mode, save)

    def set_thumbnail_size(self, size, save=True):
        """
        Définit la taille des vignettes

        Args:
            size: 'small', 'normal' ou 'large'
            save: Si True, sauvegarde immédiatement

        Returns:
            True si la sauvegarde a réussi
        """
        if size not in ['small', 'normal', 'large']:
            print(f"Taille de vignette invalide : {size}")
            return False
        return self.set('thumbnail_size', size, save)

    def set_font_size_offset(self, offset, save=True):
        """
        Définit l'offset de taille de police

        Args:
            offset: Offset entier (0 = taille par défaut)
            save: Si True, sauvegarde immédiatement

        Returns:
            True si la sauvegarde a réussi
        """
        return self.set('font_size_offset', offset, save)

    def set_buttons_column_width(self, width, save=True):
        """
        Définit la largeur de la colonne de boutons

        Args:
            width: Largeur en pixels
            save: Si True, sauvegarde immédiatement

        Returns:
            True si la sauvegarde a réussi
        """
        return self.set('buttons_column_width', width, save)

    def get_recent_files(self):
        """
        Récupère la liste des fichiers récemment ouverts

        Returns:
            Liste des chemins de fichiers récents
        """
        return self.config.get('recent_files', self.DEFAULT_CONFIG['recent_files'])

    def set_recent_files(self, recent_files, save=True):
        """
        Définit la liste complète des fichiers récents

        Args:
            recent_files: Liste des chemins de fichiers
            save: Si True, sauvegarde immédiatement

        Returns:
            True si la sauvegarde a réussi
        """
        return self.set('recent_files', recent_files, save)

    def add_recent_file(self, filepath, max_files=10, save=True):
        """
        Ajoute un fichier à la liste des fichiers récents

        Args:
            filepath: Chemin du fichier à ajouter
            max_files: Nombre maximum de fichiers à conserver (défaut: 10)
            save: Si True, sauvegarde immédiatement

        Returns:
            True si la sauvegarde a réussi
        """
        # Normalise le chemin
        filepath = os.path.abspath(filepath)

        # Récupère la liste actuelle
        recent_files = self.get_recent_files().copy()

        # Retire le fichier s'il existe déjà (pour le remettre en premier)
        if filepath in recent_files:
            recent_files.remove(filepath)

        # Ajoute en première position
        recent_files.insert(0, filepath)

        # Limite au nombre maximum
        recent_files = recent_files[:max_files]

        # Sauvegarde
        return self.set('recent_files', recent_files, save)

    def clean_recent_files(self, save=True):
        """
        Nettoie la liste des fichiers récents en supprimant les fichiers qui n'existent plus

        Args:
            save: Si True, sauvegarde immédiatement

        Returns:
            True si la sauvegarde a réussi
        """
        recent_files = self.get_recent_files()
        cleaned = [path for path in recent_files if os.path.exists(path)]

        if len(cleaned) != len(recent_files):
            return self.set('recent_files', cleaned, save)
        return True

    # ===== Méthodes utilitaires =====

    def reset_to_defaults(self):
        """
        Réinitialise tous les paramètres aux valeurs par défaut

        Returns:
            True si la sauvegarde a réussi
        """
        self.config = self.DEFAULT_CONFIG.copy()
        return self.save_config()

    def get_config_file_path(self):
        """
        Retourne le chemin complet du fichier de configuration

        Returns:
            Chemin absolu du fichier de configuration
        """
        return self.config_file

    # ===== Config barre d'icônes (fichier séparé, non effacé par reset) =====

    @property
    def _icon_toolbar_config_file(self):
        return os.path.join(self.config_dir, ".mosaicview_icon_toolbar.json")

    def _read_icon_toolbar_config(self):
        """Lit le fichier de config icon_toolbar, retourne un dict (vide si absent/erreur)."""
        try:
            if os.path.exists(self._icon_toolbar_config_file):
                with open(self._icon_toolbar_config_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            print(f"Erreur lecture icon_toolbar config : {e}")
        return {}

    def _write_icon_toolbar_config(self, data):
        """Écrit le dict dans le fichier de config icon_toolbar."""
        try:
            with open(self._icon_toolbar_config_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"Erreur sauvegarde icon_toolbar config : {e}")
            return False

    def get_icon_toolbar_layout(self):
        """Retourne la liste ordonnée des IDs d'icônes de la barre, ou None si pas encore sauvegardée."""
        return self.config.get('icon_toolbar_layout') or None

    def set_icon_toolbar_layout(self, layout):
        """Sauvegarde la liste ordonnée des IDs d'icônes de la barre."""
        return self.set('icon_toolbar_layout', list(layout))

    def get_icon_size_index(self):
        """Retourne l'index de taille des icônes (0=grande, 1=moyenne, 2=petite)."""
        return int(self.config.get('icon_size_index', 0))

    def set_icon_size_index(self, index):
        """Persiste l'index de taille des icônes."""
        return self.set('icon_size_index', int(index))

    def get_show_thumb_slider(self):
        return bool(self.config.get('show_thumb_slider', True))

    def set_show_thumb_slider(self, value):
        return self.set('show_thumb_slider', bool(value))

    def get_show_lang_combo(self):
        return bool(self.config.get('show_lang_combo', True))

    def set_show_lang_combo(self, value):
        return self.set('show_lang_combo', bool(value))

    def get_use_icon_toolbar(self):
        """Retourne True si la barre d'icônes est active (TEMPORAIRE — dev)."""
        return self.config.get('use_icon_toolbar', False)

    def set_use_icon_toolbar(self, value):
        """Persiste le flag barre d'icônes (TEMPORAIRE — dev)."""
        return self.set('use_icon_toolbar', bool(value))

    def get_split_active(self):
        """Retourne True si l'interface est scindée en deux panneaux."""
        return bool(self.config.get('split_active', False))

    def set_split_active(self, value):
        """Persiste l'état de scission de l'interface."""
        return self.set('split_active', bool(value))

    def get_split_ratio(self):
        """Retourne le ratio de division entre les deux panneaux (0.0–1.0, défaut 0.5)."""
        return float(self.config.get('split_ratio', 0.5))

    def set_split_ratio(self, value):
        """Persiste le ratio de division entre les deux panneaux."""
        return self.set('split_ratio', float(value))

    # ── Disposition toolbar panneau 2 ─────────────────────────────────────────

    def get_icon_toolbar_layout_panel2(self):
        return self.config.get('icon_toolbar_layout_panel2') or None

    def set_icon_toolbar_layout_panel2(self, layout):
        return self.set('icon_toolbar_layout_panel2', list(layout))

    def get_icon_size_index_panel2(self):
        return int(self.config.get('icon_size_index_panel2', 0))

    def set_icon_size_index_panel2(self, index):
        return self.set('icon_size_index_panel2', int(index))

    def get_show_thumb_slider_panel2(self):
        return bool(self.config.get('show_thumb_slider_panel2', True))

    def set_show_thumb_slider_panel2(self, value):
        return self.set('show_thumb_slider_panel2', bool(value))

    def get_show_lang_combo_panel2(self):
        return bool(self.config.get('show_lang_combo_panel2', True))

    def set_show_lang_combo_panel2(self, value):
        return self.set('show_lang_combo_panel2', bool(value))

    def get_buttons_column_width_panel2(self):
        return self.config.get('buttons_column_width_panel2', None)

    def set_buttons_column_width_panel2(self, width, save=True):
        return self.set('buttons_column_width_panel2', int(width), save)


class Panel2Config:
    """Wrapper autour de ConfigManager qui lit/écrit les clés dédiées au panneau 2.
    Expose exactement les mêmes méthodes que ConfigManager pour la toolbar,
    mais redirige vers les clés *_panel2 afin de ne pas écraser la config de panel1."""

    def __init__(self, cfg: "ConfigManager"):
        self._cfg = cfg

    def get_icon_toolbar_layout(self):
        return self._cfg.get_icon_toolbar_layout_panel2()

    def set_icon_toolbar_layout(self, layout):
        return self._cfg.set_icon_toolbar_layout_panel2(layout)

    def get_icon_size_index(self):
        return self._cfg.get_icon_size_index_panel2()

    def set_icon_size_index(self, index):
        return self._cfg.set_icon_size_index_panel2(index)

    def get_show_thumb_slider(self):
        return self._cfg.get_show_thumb_slider_panel2()

    def set_show_thumb_slider(self, value):
        return self._cfg.set_show_thumb_slider_panel2(value)

    def get_show_lang_combo(self):
        return self._cfg.get_show_lang_combo_panel2()

    def set_show_lang_combo(self, value):
        return self._cfg.set_show_lang_combo_panel2(value)


# Instance globale du gestionnaire de configuration
# Sera initialisée dans le fichier principal
_config_manager = None


def init_config_manager(config_dir=None):
    """
    Initialise le gestionnaire de configuration global

    Args:
        config_dir: Répertoire où stocker la configuration

    Returns:
        Instance de ConfigManager
    """
    global _config_manager
    _config_manager = ConfigManager(config_dir)
    return _config_manager


def get_config_manager():
    """
    Récupère l'instance globale du gestionnaire de configuration

    Returns:
        Instance de ConfigManager
    """
    return _config_manager
