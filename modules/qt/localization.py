"""
Module de gestion de la localisation pour MosaicView
Gère le chargement et l'accès aux traductions dans différentes langues
"""

import json
import os
import locale
import sys
import threading
from modules.qt.config_manager import get_config_manager

class LocalizationManager:
    """Gestionnaire de localisation pour l'application"""

    # Langues disponibles (triées alphabétiquement)
    # Alphabet latin d'abord, puis autres alphabets, puis langues fictives
    AVAILABLE_LANGUAGES = {
        'id': 'Bahasa Indonesia',
        'ms': 'Bahasa Melayu',
        'cs': 'čeština',
        'da': 'dansk',
        'de': 'Deutsch',
        'et': 'eesti',
        'en': 'English',
        'es': 'Español',
        'fr': 'Français',
        'ga': 'Gaeilge',
        'hr': 'hrvatski',
        'is': 'íslenska',
        'it': 'Italiano',
        'lv': 'latviešu',
        'lt': 'lietuvių',
        'hu': 'magyar',
        'mt': 'Malti',
        'nl': 'Nederlands',
        'no': 'norsk',
        'pl': 'polski',
        'pt': 'Português',
        'ro': 'română',
        'sk': 'slovenčina',
        'sl': 'slovenščina',
        'fi': 'suomi',
        'sv': 'svenska',
        'vi': 'Tiếng Việt',
        'tr': 'Türkçe',
        'el': 'Ελληνικά',
        'bg': 'български',
        'uk': 'українська',
        'hy': 'հայերեն',
        'ar': 'العربية',
        'hi': 'हिन्दी',
        'th': 'ไทย',
        'ta': 'தமிழ்',
        'zh-CN': '中文 (简体)',
        'zh-TW': '中文 (繁體)',
        'ja': '日本語',
        'ko': '한국어',
        # Langues fictives
        'tlh': 'tlhIngan Hol',
        'tlh-piqad': '\uF8E4\uF8D7\uF8DC\uF8D0\uF8DB \uF8D6\uF8DD\uF8D9',  # pIqaD (codepoints CSUR U+F8D0–U+F8FF via pIqaD qolqoS)
        'sjn': 'Sindarin',
        'sjn-tengwar': '\ue024\ue004\ue050\ue044\ue020\ue040\ue010\ue044',  # Tengwar CSUR "Sindarin" (U+E000–U+E07F via Alcarin Tengwar)
        'qya': 'Quenya',
        'qya-tengwar': '\ue003\ue046\ue010\ue043\ue040'  # Tengwar CSUR "Quenya" (U+E000–U+E07F via Alcarin Tengwar)
    }

    # Langue par défaut
    DEFAULT_LANGUAGE = 'en'

    def __init__(self):
        """
        Initialise le gestionnaire de localisation
        """
        self.locales_dir = self.resource_path("locales")
        self.current_language = None
        self.translations = {}

        # Cache mémoire pour les traductions (tous les fichiers JSON chargés au démarrage)
        self._translations_cache = {}
        self._cache_lock = threading.Lock()

        # Détermine la langue active avant le chargement
        active_lang = get_config_manager().get_language() if get_config_manager() else None
        if active_lang is None:
            active_lang = self._detect_system_language_static()

        # Charge la langue active en synchrone (bloquant mais rapide : 1 fichier)
        self._load_one_translation(active_lang)

        # Charge toutes les autres langues en arrière-plan
        t = threading.Thread(
            target=self._preload_remaining,
            args=(active_lang,),
            daemon=True,
        )
        t.start()

        # Charge la langue initiale
        self.load_language()

    def resource_path(self, relative_path):
        """
        Obtient le chemin absolu vers une ressource (compatible PyInstaller)

        Args:
            relative_path: Chemin relatif vers la ressource

        Returns:
            Chemin absolu vers la ressource
        """
        try:
            # PyInstaller crée un dossier temporaire et stocke le chemin dans _MEIPASS
            base_path = sys._MEIPASS
        except Exception:
            base_path = os.path.abspath(".")
        return os.path.join(base_path, relative_path)

    def _detect_system_language_static(self):
        """Version statique de detect_system_language (appelable avant load_language)."""
        try:
            system_locale = locale.getdefaultlocale()[0]
            if system_locale:
                lang_code = system_locale.split('_')[0].lower()
                if lang_code in self.AVAILABLE_LANGUAGES:
                    return lang_code
        except Exception:
            pass
        return self.DEFAULT_LANGUAGE

    def _load_one_translation(self, lang_code):
        """Charge un seul fichier de traduction dans le cache (thread-safe)."""
        if lang_code not in self.AVAILABLE_LANGUAGES:
            return
        translation_file = os.path.join(self.locales_dir, f"{lang_code}.json")
        if not os.path.isfile(translation_file):
            return
        try:
            with open(translation_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            with self._cache_lock:
                self._translations_cache[lang_code] = data
        except Exception as e:
            print(f"Erreur lors du chargement de {lang_code}.json: {e}")

    def _preload_remaining(self, skip_lang):
        """Charge en arrière-plan tous les fichiers de traduction sauf skip_lang."""
        if not os.path.exists(self.locales_dir):
            return
        for filename in os.listdir(self.locales_dir):
            if not filename.endswith('.json'):
                continue
            lang_code = filename[:-5]
            if lang_code == skip_lang:
                continue
            if lang_code not in self.AVAILABLE_LANGUAGES:
                continue
            with self._cache_lock:
                if lang_code in self._translations_cache:
                    continue
            self._load_one_translation(lang_code)

    def detect_system_language(self):
        """
        Détecte la langue du système d'exploitation

        Returns:
            Code de langue (ex: 'fr', 'en') ou DEFAULT_LANGUAGE si non supporté
        """
        try:
            # Obtient la locale du système
            system_locale = locale.getdefaultlocale()[0]

            if system_locale:
                # Extrait le code de langue (ex: 'fr_FR' -> 'fr')
                lang_code = system_locale.split('_')[0].lower()

                # Vérifie si la langue est supportée
                if lang_code in self.AVAILABLE_LANGUAGES:
                    return lang_code
        except Exception as e:
            print(f"Erreur lors de la détection de la langue système : {e}")

        # Retourne la langue par défaut si la détection échoue
        return self.DEFAULT_LANGUAGE

    def load_language_config(self):
        """
        Charge la configuration de langue depuis le gestionnaire de config centralisé

        Returns:
            Code de langue ou None si pas de config sauvegardée
        """
        config_manager = get_config_manager()
        if config_manager:
            return config_manager.get_language()
        return None

    def save_language_config(self, lang_code):
        """
        Sauvegarde la langue choisie via le gestionnaire de config centralisé

        Args:
            lang_code: Code de la langue à sauvegarder
        """
        config_manager = get_config_manager()
        if config_manager:
            config_manager.set_language(lang_code)

    def load_language(self, lang_code=None):
        """
        Charge les traductions pour une langue depuis le cache mémoire

        Args:
            lang_code: Code de la langue à charger (ex: 'fr', 'en')
                      Si None, charge depuis la config ou détecte la langue système

        Returns:
            True si le chargement a réussi, False sinon
        """
        # Détermine quelle langue charger
        should_save_config = False
        if lang_code is None:
            # 1. Essaie de charger depuis la configuration
            lang_code = self.load_language_config()

            # 2. Si pas de config, détecte la langue système
            if lang_code is None:
                lang_code = self.detect_system_language()
                should_save_config = True  # Sauvegarder la langue détectée

        # Vérifie que la langue est supportée
        if lang_code not in self.AVAILABLE_LANGUAGES:
            lang_code = self.DEFAULT_LANGUAGE

        # Charger depuis le cache (thread-safe)
        with self._cache_lock:
            cached = self._translations_cache.get(lang_code)
            default_cached = self._translations_cache.get(self.DEFAULT_LANGUAGE)

        if cached is not None:
            self.translations = cached
            self.current_language = lang_code

            # Sauvegarder la langue si elle a été auto-détectée
            if should_save_config:
                self.save_language_config(lang_code)

            return True

        # Langue pas encore en cache (thread de fond pas encore arrivé) — charger maintenant
        self._load_one_translation(lang_code)
        with self._cache_lock:
            cached = self._translations_cache.get(lang_code)

        if cached is not None:
            self.translations = cached
            self.current_language = lang_code
            if should_save_config:
                self.save_language_config(lang_code)
            return True

        # Fallback ultime
        print(f"Langue {lang_code} non trouvée en cache, utilisation de la langue par défaut")
        if lang_code != self.DEFAULT_LANGUAGE and default_cached is not None:
            self.translations = default_cached
            self.current_language = self.DEFAULT_LANGUAGE
            return True

        return False

    def change_language(self, lang_code):
        """
        Change la langue de l'application

        Args:
            lang_code: Code de la nouvelle langue

        Returns:
            True si le changement a réussi, False sinon
        """
        if lang_code not in self.AVAILABLE_LANGUAGES:
            print(f"Langue non supportée : {lang_code}")
            return False

        # Charge la nouvelle langue
        if self.load_language(lang_code):
            # Sauvegarde le choix
            self.save_language_config(lang_code)
            return True
        return False

    def clear_cache(self):
        """
        Vide le cache des traductions (à appeler uniquement à la fermeture de l'application)
        """
        with self._cache_lock:
            self._translations_cache.clear()
        self.translations = {}

    def get(self, key_path, **kwargs):
        """
        Récupère une traduction par sa clé

        Args:
            key_path: Chemin de la clé (ex: 'buttons.open_file' ou 'messages.warnings.empty_cbz.title')
            **kwargs: Arguments pour le formatage de la chaîne (ex: count=5, name="test")

        Returns:
            Chaîne traduite ou la clé si non trouvée
        """
        # Sépare le chemin en parties
        keys = key_path.split('.')

        # Navigate through the nested dictionary
        value = self.translations
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                # Clé non trouvée, retourne le chemin comme fallback
                print(f"Traduction non trouvée : {key_path}")
                return key_path

        # Si la valeur est un dictionnaire, retourne la clé (structure incomplète)
        if isinstance(value, dict):
            print(f"Chemin de traduction incomplet : {key_path}")
            return key_path

        # Normalise les séquences \n littérales en vrais sauts de ligne
        if isinstance(value, str):
            value = value.replace("\\n", "\n")

        # Formate la chaîne si des arguments sont fournis
        if kwargs:
            try:
                return value.format(**kwargs)
            except KeyError as e:
                print(f"Paramètre manquant pour la traduction '{key_path}': {e}")
                return value

        return value

    def get_language_name(self, lang_code=None):
        """
        Récupère le nom d'une langue

        Args:
            lang_code: Code de la langue (si None, retourne la langue actuelle)

        Returns:
            Nom de la langue ou None si non trouvée
        """
        if lang_code is None:
            lang_code = self.current_language
        return self.AVAILABLE_LANGUAGES.get(lang_code)

    def get_available_languages(self):
        """
        Récupère la liste des langues disponibles

        Returns:
            Dictionnaire {code: nom} des langues disponibles
        """
        return self.AVAILABLE_LANGUAGES.copy()

    def get_current_language(self):
        """
        Récupère le code de la langue actuelle

        Returns:
            Code de la langue actuelle
        """
        return self.current_language


# Instance globale du gestionnaire de localisation
# Sera initialisée dans le fichier principal
_localization_manager = None

def init_localization():
    """
    Initialise le gestionnaire de localisation global
    """
    global _localization_manager
    _localization_manager = LocalizationManager()
    return _localization_manager

def get_localization():
    """
    Récupère l'instance globale du gestionnaire de localisation

    Returns:
        Instance de LocalizationManager
    """
    return _localization_manager

def _(key_path, **kwargs):
    """
    Fonction raccourci pour obtenir une traduction

    Args:
        key_path: Chemin de la clé de traduction
        **kwargs: Arguments pour le formatage

    Returns:
        Chaîne traduite
    """
    if _localization_manager is None:
        return key_path
    return _localization_manager.get(key_path, **kwargs)


# Langues à alphabet non-affichable dans les titres de fenêtres Windows
_LATIN_FALLBACK = {
    'sjn-tengwar': 'sjn',
    'qya-tengwar': 'qya',
    'tlh-piqad':   'tlh',
}

def _wt(key_path, **kwargs):
    """
    Traduction pour setWindowTitle : retourne la valeur en latin quand la
    langue courante utilise un alphabet non-rendu par Windows (Tengwar, pIqaD).
    """
    if _localization_manager is None:
        return key_path
    current = _localization_manager.current_language
    fallback = _LATIN_FALLBACK.get(current)
    if fallback:
        with _localization_manager._cache_lock:
            data = _localization_manager._translations_cache.get(fallback)
        if data is None:
            _localization_manager._load_one_translation(fallback)
            with _localization_manager._cache_lock:
                data = _localization_manager._translations_cache.get(fallback)
        if data is not None:
            keys = key_path.split('.')
            value = data
            for key in keys:
                if isinstance(value, dict) and key in value:
                    value = value[key]
                else:
                    value = None
                    break
            if isinstance(value, str):
                value = value.replace("\\n", "\n")
                if kwargs:
                    try:
                        return value.format(**kwargs)
                    except KeyError:
                        return value
                return value
    return _localization_manager.get(key_path, **kwargs)
