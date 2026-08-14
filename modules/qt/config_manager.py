"""
Module de gestion centralisée de la configuration pour MosaicView
Gère tous les paramètres de l'application dans un fichier JSON unique
"""

import base64
import json
import os
import shutil
import sys
import tempfile


def _dpapi_encrypt(plaintext: str) -> str:
    """Chiffre plaintext via DPAPI (Windows). Retourne une chaîne base64."""
    try:
        import win32crypt
        encrypted = win32crypt.CryptProtectData(
            plaintext.encode('utf-8'), None, None, None, None, 0
        )
        return base64.b64encode(encrypted).decode('ascii')
    except Exception:
        return ''


def _dpapi_decrypt(b64_cipher: str) -> str:
    """Déchiffre une chaîne base64 DPAPI. Retourne '' en cas d'échec."""
    try:
        import win32crypt
        encrypted = base64.b64decode(b64_cipher)
        _desc, plaintext = win32crypt.CryptUnprotectData(
            encrypted, None, None, None, 0
        )
        return plaintext.decode('utf-8')
    except Exception:
        return ''


class ConfigManager:
    """Gestionnaire centralisé de configuration pour l'application"""

    # Nom du fichier de configuration
    CONFIG_FILENAME = ".mosaicview_config.json"

    # Valeurs par défaut pour tous les paramètres
    DEFAULT_CONFIG = {
        'language': None,  # None = détection automatique
        'sidebar_collapsed': True,   # Barre d'icônes rabattue par défaut
        'sidebar_collapsed_panel2': True,  # Barre d'icônes panel2 rabattue par défaut
        'minimap_visible': False,   # Minimap cachée par défaut
        'minimap_visible_panel2': False,  # Minimap panel2 cachée par défaut
        'fullscreen': False,  # Mode fenêtré par défaut
        'maximized': False,  # Fenêtre maximisée par défaut (False)
        'window_position': None,  # None = centré par défaut
        'window_size': {'width': 1240, 'height': 780},  # Taille par défaut
        'dark_mode': False,  # Mode clair par défaut
        'thumbnail_size': 'normal',  # Taille normale par défaut ('small', 'normal', 'large')
        'thumbnail_size_panel2': 'normal',  # Idem, distincte pour le panel2
        'font_size_offset': 0,  # Offset additif pour la taille de police (0 = taille par défaut)
        'buttons_column_width': 220,  # Largeur de la colonne de boutons (par défaut 220px)
        'recent_files': [],  # Liste des fichiers récemment ouverts (max 10)
        'recent_dbs':   [],  # Liste des bases de données récemment ouvertes (max 10)
        'use_icon_toolbar': False,  # TEMPORAIRE (dev) — barre d'icônes active
        'comicvine_api_key': '',    # Clé API ComicVine (chiffrée DPAPI, base64)
        'scan_capabilities': {},    # {device_id: {"resolutions": [...], "color_modes": [...], "max_width", "max_height"}} — cache par scanner, voir skill scan
        'scan_last_settings': {},   # {"device_id", "dpi", "color_mode"} — dernier choix utilisateur dans ScanDialog, voir skill scan
    }

    def __init__(self, config_dir=None):
        """
        Initialise le gestionnaire de configuration

        Args:
            config_dir: Répertoire où est stocké le fichier de configuration (optionnel)
                       Si None, utilise %APPDATA%\\MosaicView
        """
        if config_dir is None:
            # Utilise %APPDATA%\MosaicView (config persistante, distincte des fichiers temporaires)
            config_dir = os.path.join(os.environ["APPDATA"], "MosaicView")

            # Crée le répertoire s'il n'existe pas
            if not os.path.exists(config_dir):
                os.makedirs(config_dir, exist_ok=True)

        self.config_dir = config_dir
        self.config_file = os.path.join(self.config_dir, self.CONFIG_FILENAME)

        # Migration depuis l'ancien emplacement (%TEMP%\MosaicViewTemp) : déplace les
        # fichiers de config existants s'ils n'ont pas déjà été migrés.
        self._migrate_from_temp()

        self.config = self.DEFAULT_CONFIG.copy()

        # Charge la configuration existante
        config_loaded = self.load_config()

        # Sauvegarde toujours pour :
        # - Créer le fichier s'il n'existe pas
        # - Mettre à jour avec les nouvelles clés si la config a été chargée
        self.save_config()

    def _migrate_from_temp(self):
        """Déplace (pas copie) la config existante de %TEMP%\\MosaicViewTemp vers
        %APPDATA%\\MosaicView si elle n'a pas encore été migrée. Sans effet si le
        fichier cible existe déjà ou si l'ancien fichier est absent."""
        if os.path.exists(self.config_file):
            return
        old_dir = os.path.join(tempfile.gettempdir(), "MosaicViewTemp")
        for filename in (self.CONFIG_FILENAME, ".mosaicview_icon_toolbar.json"):
            old_path = os.path.join(old_dir, filename)
            new_path = os.path.join(self.config_dir, filename)
            if os.path.exists(old_path) and not os.path.exists(new_path):
                try:
                    shutil.move(old_path, new_path)
                except Exception:
                    pass

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
        except Exception:
            pass
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
        except Exception:
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

    def get_thumbnail_size_panel2(self):
        """
        Récupère la taille des vignettes du panel2

        Returns:
            'small', 'normal' ou 'large'
        """
        return self.config.get('thumbnail_size_panel2', self.DEFAULT_CONFIG['thumbnail_size_panel2'])

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

    def get_minimap_visible(self):
        """Récupère l'état de la minimap du panneau 1 (True = visible, False = cachée)"""
        return self.config.get('minimap_visible', self.DEFAULT_CONFIG['minimap_visible'])

    def set_minimap_visible(self, visible, save=True):
        return self.set('minimap_visible', visible, save)

    def get_minimap_visible_panel2(self):
        return self.config.get('minimap_visible_panel2', self.DEFAULT_CONFIG['minimap_visible_panel2'])

    def set_minimap_visible_panel2(self, visible, save=True):
        return self.set('minimap_visible_panel2', visible, save)

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
            return False
        return self.set('thumbnail_size', size, save)

    def set_thumbnail_size_panel2(self, size, save=True):
        """
        Définit la taille des vignettes du panel2

        Args:
            size: 'small', 'normal' ou 'large'
            save: Si True, sauvegarde immédiatement

        Returns:
            True si la sauvegarde a réussi
        """
        if size not in ['small', 'normal', 'large']:
            return False
        return self.set('thumbnail_size_panel2', size, save)

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

    def get_recent_dbs(self):
        return self.config.get('recent_dbs', self.DEFAULT_CONFIG['recent_dbs'])

    def set_recent_dbs(self, recent_dbs, save=True):
        return self.set('recent_dbs', recent_dbs, save)

    def add_recent_db(self, filepath, max_files=10, save=True):
        filepath = os.path.abspath(filepath)
        recent = self.get_recent_dbs().copy()
        if filepath in recent:
            recent.remove(filepath)
        recent.insert(0, filepath)
        recent = recent[:max_files]
        return self.set('recent_dbs', recent, save)

    def clean_recent_dbs(self, save=True):
        recent = self.get_recent_dbs()
        cleaned = [p for p in recent if os.path.exists(p)]
        if len(cleaned) != len(recent):
            return self.set('recent_dbs', cleaned, save)
        return True

    # ── Clé API ComicVine (chiffrée DPAPI) ───────────────────────────────────

    def get_comicvine_api_key(self) -> str:
        """Retourne la clé API ComicVine déchiffrée, ou '' si absente/illisible."""
        raw = self.config.get('comicvine_api_key', '').strip()
        if not raw:
            return ''
        decrypted = _dpapi_decrypt(raw)
        if decrypted:
            return decrypted
        # Migration : valeur en clair héritée — on la rechiffre immédiatement
        self.set_comicvine_api_key(raw)
        return raw

    def set_comicvine_api_key(self, key: str, save: bool = True) -> bool:
        """Chiffre key via DPAPI et la persiste dans la config."""
        encrypted = _dpapi_encrypt(key) if key else ''
        return self.set('comicvine_api_key', encrypted, save)

    # ===== Méthodes utilitaires =====

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
        except Exception:
            pass
        return {}

    def _write_icon_toolbar_config(self, data):
        """Écrit le dict dans le fichier de config icon_toolbar."""
        try:
            with open(self._icon_toolbar_config_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            return True
        except Exception:
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

    def get_renumber_mode(self):
        """Retourne le mode de renumérotation persisté (0=OFF, 1=auto, 2=simple)."""
        return int(self.config.get('renumber_mode', 1))

    def set_renumber_mode(self, mode):
        """Persiste le mode de renumérotation."""
        return self.set('renumber_mode', int(mode))

    def get_straighten_mode(self):
        """Retourne le mode de redressement persisté (0=manuel, 1=automatique)."""
        return int(self.config.get('straighten_mode', 0))

    def set_straighten_mode(self, mode):
        """Persiste le mode de redressement."""
        return self.set('straighten_mode', int(mode))

    def get_sharpness_mode(self):
        """Retourne le mode de netteté persisté de la visionneuse (0=sharpness, 1=unsharp)."""
        return int(self.config.get('sharpness_mode', 0))

    def set_sharpness_mode(self, mode):
        """Persiste le mode de netteté de la visionneuse."""
        return self.set('sharpness_mode', int(mode))

    def get_zip_compression_level(self):
        """Retourne le niveau de compression ZIP par défaut à l'enregistrement (0-9, défaut 0=store)."""
        return int(self.config.get('zip_compression_level', 0))

    def set_zip_compression_level(self, level):
        """Persiste le niveau de compression ZIP par défaut."""
        return self.set('zip_compression_level', int(level))

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

    def get_renumber_mode_panel2(self):
        """Retourne le mode de renumérotation persisté du panneau 2 (0=OFF, 1=auto, 2=simple)."""
        return int(self.config.get('renumber_mode_panel2', 1))

    def set_renumber_mode_panel2(self, mode):
        """Persiste le mode de renumérotation du panneau 2."""
        return self.set('renumber_mode_panel2', int(mode))

    def get_straighten_mode_panel2(self):
        """Retourne le mode de redressement persisté du panneau 2 (0=manuel, 1=automatique)."""
        return int(self.config.get('straighten_mode_panel2', 0))

    def set_straighten_mode_panel2(self, mode):
        """Persiste le mode de redressement du panneau 2."""
        return self.set('straighten_mode_panel2', int(mode))

    def get_sharpness_mode_panel2(self):
        """Retourne le mode de netteté persisté de la visionneuse du panneau 2 (0=sharpness, 1=unsharp)."""
        return int(self.config.get('sharpness_mode_panel2', 0))

    def set_sharpness_mode_panel2(self, mode):
        """Persiste le mode de netteté de la visionneuse du panneau 2."""
        return self.set('sharpness_mode_panel2', int(mode))

    def get_zip_compression_level_panel2(self):
        """Retourne le niveau de compression ZIP par défaut du panneau 2 (0-9, défaut 0=store)."""
        return int(self.config.get('zip_compression_level_panel2', 0))

    def set_zip_compression_level_panel2(self, level):
        """Persiste le niveau de compression ZIP par défaut du panneau 2."""
        return self.set('zip_compression_level_panel2', int(level))

    # ── Marques-pages ─────────────────────────────────────────────────────────

    def get_bookmarks(self) -> dict:
        """Retourne le dict {chemin_archive: index_page} de tous les marques-pages."""
        return self.config.get('bookmarks', {})

    def get_bookmark(self, filepath: str) -> int | None:
        """Retourne l'index de page mémorisé pour filepath, ou None."""
        return self.get_bookmarks().get(os.path.abspath(filepath))

    def set_bookmark(self, filepath: str, page_idx: int, save: bool = True):
        """Mémorise page_idx pour filepath."""
        bookmarks = self.get_bookmarks().copy()
        bookmarks[os.path.abspath(filepath)] = page_idx
        return self.set('bookmarks', bookmarks, save)

    def remove_bookmark(self, filepath: str, save: bool = True):
        """Supprime le marque-page de filepath (sans erreur si absent)."""
        bookmarks = self.get_bookmarks().copy()
        key = os.path.abspath(filepath)
        if key in bookmarks:
            del bookmarks[key]
            return self.set('bookmarks', bookmarks, save)
        return True

    def clear_bookmarks(self, save: bool = True):
        """Supprime tous les marques-pages."""
        return self.set('bookmarks', {}, save)

    def has_any_bookmark(self) -> bool:
        """Retourne True si au moins un marque-page existe."""
        return bool(self.get_bookmarks())

    # ── Capacités de scanner (cache) ─────────────────────────────────────────

    def get_all_scan_capabilities(self) -> dict:
        """Retourne le dict {device_id: caps} de tous les scanners connus."""
        return self.config.get('scan_capabilities', {})

    def get_scan_capabilities(self, device_id: str) -> dict | None:
        """Retourne les capacités mémorisées pour device_id (dict avec
        "resolutions"/"color_modes"/"max_width"/"max_height"), ou None si ce
        device n'a jamais été interrogé avec succès. Voir skill scan."""
        return self.get_all_scan_capabilities().get(device_id)

    def set_scan_capabilities(self, device_id: str, caps: dict, save: bool = True):
        """Mémorise les capacités interrogées pour device_id — évite de
        réinterroger le scanner à chaque ouverture de ScanDialog pour un
        device déjà connu (voir skill scan)."""
        all_caps = self.get_all_scan_capabilities().copy()
        all_caps[device_id] = caps
        return self.set('scan_capabilities', all_caps, save)

    def clear_scan_capabilities(self, save: bool = True):
        """Supprime le cache de capacités de tous les scanners."""
        return self.set('scan_capabilities', {}, save)

    # ── Derniers réglages de scan choisis ────────────────────────────────────

    def get_scan_last_settings(self) -> dict:
        """Retourne le dernier device/dpi/mode couleur choisis dans ScanDialog
        ({"device_id", "dpi", "color_mode"}), ou {} si jamais scanné. Voir
        skill scan."""
        return self.config.get('scan_last_settings', {})

    def set_scan_last_settings(self, device_id: str, dpi: int, color_mode: str, save: bool = True):
        """Mémorise le device/dpi/mode couleur choisis au dernier lancement de
        scan, pour les représélectionner à la prochaine ouverture de
        ScanDialog (voir skill scan)."""
        return self.set('scan_last_settings', {
            "device_id": device_id,
            "dpi": dpi,
            "color_mode": color_mode,
        }, save)


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

    def get_renumber_mode(self):
        return self._cfg.get_renumber_mode_panel2()

    def set_renumber_mode(self, mode):
        return self._cfg.set_renumber_mode_panel2(mode)

    def get_straighten_mode(self):
        return self._cfg.get_straighten_mode_panel2()

    def set_straighten_mode(self, mode):
        return self._cfg.set_straighten_mode_panel2(mode)

    def get_sharpness_mode(self):
        return self._cfg.get_sharpness_mode_panel2()

    def set_sharpness_mode(self, mode):
        return self._cfg.set_sharpness_mode_panel2(mode)

    def get_zip_compression_level(self):
        return self._cfg.get_zip_compression_level_panel2()

    def set_zip_compression_level(self, level):
        return self._cfg.set_zip_compression_level_panel2(level)

    def get_thumbnail_size(self):
        return self._cfg.get_thumbnail_size_panel2()

    def set_thumbnail_size(self, size, save=True):
        return self._cfg.set_thumbnail_size_panel2(size, save)


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
