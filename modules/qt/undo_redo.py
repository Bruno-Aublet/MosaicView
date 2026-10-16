# -------------------------
# Système Annuler/Refaire (logique data)
# -------------------------

import itertools

# Taille maximale de l'historique
MAX_HISTORY = 20

_KEY_COUNTER = itertools.count(1)

# Numéro unique de chaque état de l'historique (jamais réutilisé) : repère
# d'un point d'historique pour ce qui y est rattaché (history_point_key).
_SNAPSHOT_COUNTER = itertools.count(1)


def entry_key(entry):
    """Jeton qui identifie une entrée d'une snapshot à l'autre (« _original_id »
    des snapshots). Jamais id(entry) seul : un id est réattribué par Python à
    un nouvel objet une fois l'ancien libéré, et une restauration prendrait
    alors une autre page pour l'ancienne (bytes et nom de la snapshot, mais
    DPI, dimensions, source… de l'autre page).

    Le jeton est stocké dans l'entrée avec l'id de l'objet qui l'a reçu : une
    copie de l'entrée (dict(entry)) porte un id différent et reçoit donc son
    propre jeton au lieu de partager celui de l'original."""
    key = entry.get("_undo_key")
    if key is None or key[1] != id(entry):
        key = (next(_KEY_COUNTER), id(entry))
        entry["_undo_key"] = key
    return key[0]


def adopt_entry_key(entry, token):
    """Donne à `entry` (recréée depuis une snapshot) le jeton `token` de
    l'entrée qu'elle remplace, pour que les snapshots suivantes la reconnaissent."""
    entry["_undo_key"] = (token, id(entry))


# Champs d'une entrée hors de "meta" dans une snapshot : ceux qu'elle stocke à
# part (bytes, nom…), et ceux propres à l'interface ou aux caches, qui ne
# décrivent pas la page (widgets, images décodées, vignettes, empreinte,
# position, dimensions relues depuis les bytes).
_NOT_IN_META = {
    "orig_name", "bytes", "extension", "is_image", "is_dir", "is_corrupted",
    "corruption_reason", "img", "name_entry", "name_var", "ext_label", "img_id",
    "text_id", "large_thumb_pil", "_thumbnail", "_hash", "_real_idx",
    "_undo_key", "_is_duplicate", "_duplicate_group", "img_width", "img_height",
}


def _entry_metadata(entry):
    """Propriétés de la page (animation, DPI, archive d'origine…) à conserver
    dans une snapshot. Une page recréée par une restauration (après une
    suppression, une coupe, une découpe…) les reprend : sans elles, un GIF
    animé redevenait une image fixe et l'opération suivante n'en gardait que
    la première frame. Copie superficielle, listes copiées."""
    meta = {}
    for key, value in entry.items():
        if key in _NOT_IN_META or key.startswith(("qt_", "tk_")):
            continue
        meta[key] = list(value) if isinstance(value, list) else value
    return meta


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
            'meta': _entry_metadata(e),
        }
        # Jeton de l'entrée, pour réutiliser l'objet existant à la restauration
        entry_copy['_original_id'] = entry_key(e)
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
        # bytes.__ne__ court-circuite déjà sur l'identité puis sur la longueur :
        # le contenu n'est comparé que pour deux objets distincts de même taille
        # (ex. miroir d'un BMP, dont la taille ne change jamais).
        last_bytes = last_entries[i]['bytes']
        current_bytes = current_entries[i]['bytes']
        if (last_bytes is None) != (current_bytes is None):
            return False
        if last_bytes is not None and current_bytes is not None:
            if last_bytes != current_bytes:
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

    # Sélection courante : jetons des objets sélectionnés dans images_data
    selected_ids = {entry_key(state.images_data[i])
                    for i in state.selected_indices
                    if i < len(state.images_data)}

    # Met à jour la sélection de l'état précédent avec les pages sélectionnées
    # qui y figurent. Par jeton, jamais par nom : deux pages peuvent porter le
    # même nom, et la restauration sélectionnerait alors les deux.
    if len(state.history) > 0:
        prev_entries = state.history[state.history_index]['entries']
        state.history[state.history_index]['selected_keys'] = {
            ec['_original_id'] for ec in prev_entries
            if ec.get('_original_id') in selected_ids
        }

    # Le nouvel état reçoit la sélection dans entries_copy (même logique)
    selected_keys = {ec['_original_id'] for ec in entries_copy
                     if ec.get('_original_id') in selected_ids}

    saved_state = {
        'entries': entries_copy,
        'modified': state.modified,
        'needs_renumbering': state.needs_renumbering,
        'current_directory': getattr(state, 'current_directory', ""),
        'current_sort_method': state.current_sort_method,
        'current_sort_order': state.current_sort_order,
        'selected_keys': selected_keys,
        'serial': next(_SNAPSHOT_COUNTER),
    }

    state.history.append(saved_state)
    state.history_index += 1

    # Limite la taille de l'historique
    if len(state.history) > MAX_HISTORY:
        state.history.pop(0)
        state.history_index -= 1

    return True


def pop_last_state(state):
    """Retire le dernier snapshot poussé, sans toucher à images_data.
    Appelé quand un état a été sauvegardé par anticipation mais que l'action
    est annulée sans rien changer (ex. annulation d'un redimensionnement en
    cours). Le snapshot initial (index 0) n'est jamais retiré."""
    if state.history_index > 0:
        state.history.pop(state.history_index)
        state.history_index -= 1


def history_top(state):
    """Snapshot au sommet actif de l'historique (state.history[history_index]),
    None si l'historique est vide. À comparer par identité avant/après un
    save_state() "avant" pour savoir s'il a réellement poussé un état, et donc
    si pop_last_state() peut être appelé en cas d'annulation : chaque action
    sauve après elle-même, le sommet décrit déjà l'état courant, et un
    save_state() sans force n'ajoute alors rien — dépiler retirerait le
    dernier état légitime de l'utilisateur. L'indice seul ne suffit pas : il
    reste fixe une fois l'historique plein (MAX_HISTORY), le plus ancien état
    étant retiré à chaque ajout."""
    if 0 <= state.history_index < len(state.history):
        return state.history[state.history_index]
    return None


def history_point_key(state):
    """Repère durable du point d'historique courant (numéro unique de l'état
    au sommet actif), None si l'historique est vide. Jamais history_index :
    il désigne un autre état après une saturation de l'historique
    (MAX_HISTORY, le plus ancien retiré et les indices décalés) ou une
    nouvelle action après des Annuler (branche Refaire remplacée)."""
    top = history_top(state)
    return top.get('serial') if top is not None else None


def mark_history_saved(state):
    """À appeler après chaque sauvegarde réussie du document.

    Chaque snapshot mémorise `modified` tel qu'il était à sa capture : sans
    cette mise à jour, un Annuler après une sauvegarde restaurerait un état
    « non modifié » pourtant différent du fichier écrit, et la fermeture ne
    demanderait plus confirmation. Tous les snapshots sont marqués modifiés,
    sauf le sommet actif s'il décrit exactement le contenu sauvegardé."""
    for snap in state.history:
        snap['modified'] = True
    top = history_top(state)
    if top is not None and _is_state_identical(top['entries'], _create_entries_snapshot(state)):
        top['modified'] = False


def content_snapshot(state):
    """Contenu du document à un instant donné (références, aucune copie des
    bytes), à comparer plus tard avec content_unchanged. Sert aux sauvegardes
    dont la fin est différée (confirmation non modale) : le document a pu être
    modifié entre l'écriture du fichier et cette fin."""
    return _create_entries_snapshot(state)


def content_unchanged(state, snapshot):
    """True si le document a encore exactement le contenu capturé par
    content_snapshot (mêmes entrées, mêmes noms, mêmes bytes)."""
    return _is_state_identical(snapshot, _create_entries_snapshot(state))


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
