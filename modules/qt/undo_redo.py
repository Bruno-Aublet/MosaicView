# -------------------------
# Système Annuler/Refaire (logique data)
# -------------------------

# Taille maximale de l'historique
MAX_HISTORY = 20


def _create_entries_snapshot_from(entries_list):
    """Crée une copie (snapshot) d'une liste d'entrées pour l'historique.
    Les bytes sont partagés par référence (pas copiés) — ils ne sont jamais
    mutés en place, seule la référence de l'entrée change lors d'une modification.
    Retourne une liste de dicts contenant les données essentielles de chaque entrée."""
    entries_copy = []
    for e in entries_list:
        entry_copy = {
            'orig_name': e['orig_name'],
            'bytes': e['bytes'],  # référence partagée, pas de copie
            'extension': e['extension'],
            'is_image': e['is_image'],
            'is_dir': e.get('is_dir', False),
            'is_corrupted': e.get('is_corrupted', False),
            'corruption_reason': e.get('corruption_reason'),
        }
        # Associe l'ID original pour pouvoir réutiliser les objets existants
        entry_copy['_original_id'] = id(e)
        entries_copy.append(entry_copy)
    return entries_copy


def _create_entries_snapshot(state):
    """Crée une copie (snapshot) de images_data pour l'historique."""
    return _create_entries_snapshot_from(state.images_data)


def _is_state_identical(last_entries, current_entries):
    """Compare deux snapshots d'entrées pour détecter si l'état a changé.
    Retourne True si les états sont identiques."""
    if len(last_entries) != len(current_entries):
        return False
    for i in range(len(current_entries)):
        if last_entries[i]['orig_name'] != current_entries[i]['orig_name']:
            return False
        # Si les _original_id diffèrent, l'entrée a été remplacée (ex. transfert inter-panneaux)
        if last_entries[i].get('_original_id') != current_entries[i].get('_original_id'):
            return False
        # Compare les bytes : d'abord par référence (rapide), puis par taille
        last_bytes = last_entries[i]['bytes']
        current_bytes = current_entries[i]['bytes']
        if (last_bytes is None) != (current_bytes is None):
            return False
        if last_bytes is not None and current_bytes is not None:
            if last_bytes is not current_bytes and len(last_bytes) != len(current_bytes):
                return False
    return True


def save_state_data(state, force=False):
    """Sauvegarde l'état actuel dans l'historique (partie data uniquement).
    Si force=True, sauvegarde sans vérifier si l'état a changé (utile au FocusIn
    d'un champ de nom, avant toute frappe).
    Retourne True si un nouvel état a été sauvegardé, False sinon."""

    # Si on est au milieu de l'historique, supprime tout ce qui suit
    if state.history_index < len(state.history) - 1:
        state.history = state.history[:state.history_index + 1]

    # Sauvegarde une copie complète de chaque entrée
    entries_copy = _create_entries_snapshot(state)

    # Vérifie si l'état actuel est différent du dernier état sauvegardé
    if not force and len(state.history) > 0:
        last_state = state.history[state.history_index]
        if _is_state_identical(last_state['entries'], entries_copy):
            # État identique, ne sauvegarde pas
            return False

    # Sauvegarde aussi all_entries et current_directory (pour l'aplatissement de répertoires)
    all_entries_copy = None
    if hasattr(state, 'all_entries') and state.all_entries and state.all_entries is not state.images_data:
        all_entries_copy = _create_entries_snapshot_from(state.all_entries)

    # Sélection courante : ids des objets sélectionnés dans images_data
    selected_ids = {id(state.images_data[i])
                    for i in state.selected_indices
                    if i < len(state.images_data)}

    # Met à jour la sélection de l'état précédent avec les noms tels qu'ils
    # apparaissent dans CE snapshot précédent (robuste aux renommages/renumérotations).
    if len(state.history) > 0:
        prev_entries = state.history[state.history_index]['entries']
        state.history[state.history_index]['selected_names'] = {
            ec['orig_name'] for ec in prev_entries
            if ec.get('_original_id') in selected_ids
        }

    # Le nouvel état reçoit la sélection dans entries_copy (même logique)
    selected_names = {ec['orig_name'] for ec in entries_copy
                      if ec.get('_original_id') in selected_ids}

    saved_state = {
        'entries': entries_copy,
        'modified': state.modified,
        'needs_renumbering': state.needs_renumbering,
        'all_entries': all_entries_copy,
        'current_directory': getattr(state, 'current_directory', ""),
        'current_sort_method': state.current_sort_method,
        'current_sort_order': state.current_sort_order,
        'selected_names': selected_names,
    }

    state.history.append(saved_state)
    state.history_index += 1

    # Limite la taille de l'historique
    if len(state.history) > MAX_HISTORY:
        state.history.pop(0)
        state.history_index -= 1

    return True


def pop_last_state(state):
    """Annule le dernier save_state_data() si aucune modification n'a eu lieu.
    Appelé au FocusOut d'un champ de nom si le nom n'a pas changé."""
    if state.history_index > 0:
        state.history.pop(state.history_index)
        state.history_index -= 1


def reset_history(state):
    """Réinitialise l'historique (appelé lors de l'ouverture/fermeture d'un fichier)."""
    state.history = []
    state.history_index = -1


def can_undo(state):
    """Vérifie si l'action Annuler est possible."""
    return state.history_index > 0


def can_redo(state):
    """Vérifie si l'action Refaire est possible."""
    return state.history_index < len(state.history) - 1


def undo_data(state):
    """Décrémente l'index de l'historique pour annuler.
    Retourne le saved_state à restaurer, ou None si impossible."""
    if not can_undo(state):
        return None
    state.history_index -= 1
    return state.history[state.history_index]


def redo_data(state):
    """Incrémente l'index de l'historique pour refaire.
    Retourne le saved_state à restaurer, ou None si impossible."""
    if not can_redo(state):
        return None
    state.history_index += 1
    return state.history[state.history_index]
