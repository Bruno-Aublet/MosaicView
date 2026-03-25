"""
Fonctions de tri des images pour MosaicView
"""

from modules.qt import state as _state_module


def get_sort_key(entry, sort_method, natural_sort_key_func, get_image_metadata_func):
    """Retourne la clé de tri pour une entrée selon la méthode

    Args:
        entry (dict): Entrée d'image à trier
        sort_method (str): Méthode de tri ("name", "type", "weight", "width", "height", "resolution", "dpi")
        natural_sort_key_func: Fonction de tri naturel alphanumérique
        get_image_metadata_func: Fonction de récupération des métadonnées d'image

    Returns:
        Clé de tri appropriée selon la méthode
    """
    if sort_method == "name":
        return natural_sort_key_func(entry["orig_name"])
    elif sort_method == "type":
        return entry["extension"].lower()
    elif sort_method == "weight":
        bytes_data = entry.get("bytes")
        return len(bytes_data) if bytes_data is not None else 0
    elif sort_method == "width":
        metadata = get_image_metadata_func(entry)
        return metadata["size"][0] if metadata else 0
    elif sort_method == "height":
        metadata = get_image_metadata_func(entry)
        return metadata["size"][1] if metadata else 0
    elif sort_method == "resolution":
        metadata = get_image_metadata_func(entry)
        if metadata:
            width, height = metadata["size"]
            return width * height
        return 0
    elif sort_method == "dpi":
        metadata = get_image_metadata_func(entry)
        if metadata:
            dpi = metadata.get("dpi")
            if isinstance(dpi, tuple):
                return dpi[0]
            elif dpi:
                return dpi
        return 0
    return 0


def sort_images(sort_method, callbacks):
    """Trie les images selon la méthode spécifiée

    Args:
        sort_method (str): Méthode de tri
        callbacks (dict): Dictionnaire contenant:
            - save_state: Fonction de sauvegarde d'état
            - render_mosaic: Fonction de rendu de la mosaïque
            - update_button_text: Fonction de mise à jour des boutons
            - natural_sort_key: Fonction de tri naturel
            - get_image_metadata: Fonction de récupération des métadonnées
    """
    state = callbacks.get('state') or _state_module.state

    if not state.images_data:
        return

    # Bascule l'ordre si on clique deux fois sur la même méthode
    if state.current_sort_method == sort_method:
        state.current_sort_order = "desc" if state.current_sort_order == "asc" else "asc"
    else:
        state.current_sort_method = sort_method
        state.current_sort_order = "asc"

    # Tri
    reverse = (state.current_sort_order == "desc")
    state.images_data.sort(
        key=lambda e: get_sort_key(e, sort_method, callbacks["natural_sort_key"], callbacks["get_image_metadata"]),
        reverse=reverse
    )

    # Marque comme modifié
    state.modified = True

    # Sauvegarde APRÈS le tri (comme toutes les autres actions)
    callbacks["save_state"]()

    # Réaffiche
    callbacks["render_mosaic"]()
    callbacks["update_button_text"]()
