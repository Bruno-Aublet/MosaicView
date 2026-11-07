"""Commandes de la visionneuse sans effet pendant une opération en arrière-plan,
et commandes de la mosaïque refusées tant qu'une visionneuse a du travail non
validé sur les pages visées.

Une opération (rotation d'une sélection, conversion…) s'appuie sur le sommet de
l'historique pris avant elle, et y revient si elle est annulée. Un outil déjà
sélectionné quand elle démarre ne doit donc rien écrire : ses états d'historique
contiendraient les pages à moitié traitées et deviendraient le sommet, et
Annuler ne rétablirait plus le document. Une lecture de macro, opération
elle-même, passe skip_history=True et n'est pas concernée.

Sans Qt : testé directement.
"""
import functools

# Visionneuses ouvertes, tous documents confondus (tenue à jour par ImageViewer)
image_viewer_refs = []


def open_viewer_on(state):
    """La visionneuse ouverte sur `state` (le document d'un panneau), ou None.
    Il n'y en a jamais qu'une par panneau : toute création passe par ce test
    (open_image_viewer, lecture de macro depuis la mosaïque)."""
    for viewer in image_viewer_refs[:]:
        if getattr(viewer, "_doc_state", None) is state and not getattr(viewer, "_closed", True):
            return viewer
    return None


def pending_page_work_refused(parent, state, entries=None) -> bool:
    """True, avec un message, si la visionneuse ouverte sur `state` a du travail
    non validé sur l'une de ces pages (entries None : sur n'importe quelle page),
    ou enregistre une macro sur l'une d'elles. La visionneuse est non modale :
    une rotation, un redressement, un redimensionnement ou un Annuler lancé
    depuis la mosaïque changerait une page sous un travail posé en pixels de la
    page d'avant, qui serait validé au mauvais endroit."""
    viewer = open_viewer_on(state)
    try:
        if viewer is None or not (viewer._has_pending_page_work(entries)
                                  or viewer._macro_recording_on_page(entries)):
            return False
    except RuntimeError:
        return False   # repli : fenêtre déjà détruite, plus aucun travail à protéger
    from modules.qt.dialogs_qt import MsgDialog
    MsgDialog(parent, "viewer.unvalidated_work_title",
              "viewer.rotation_unvalidated_work_message").show_nonmodal()
    return True


# Tout nouveau perform_* d'un outil s'ajoute ici (garde-fou
# tests/test_viewer_busy.py) ; un perform_*_step ne sert qu'à la lecture de macro.
DOCUMENT_WRITING_COMMANDS = (
    "perform_crop", "perform_straighten", "perform_auto_straighten",
    "perform_text", "perform_shapes", "perform_paste_image", "perform_transparency",
    "perform_levels", "perform_auto_levels", "perform_brightness", "perform_saturation",
    "perform_remove_colors", "perform_compression", "perform_sharpness", "perform_unsharp",
    "perform_color_depth", "perform_restore_color_depth",
    "perform_effect", "perform_restore_effect",
    "perform_image_mode", "perform_restore_image_mode",
    "perform_rotate", "perform_flip",
)


def refused_during_operation(method):
    @functools.wraps(method)
    def guarded(self, *args, **kwargs):
        if not kwargs.get("skip_history") and self._document_busy():
            self._after_refused_commit()
            return False
        return method(self, *args, **kwargs)
    return guarded


def guard_document_commands(viewer_class):
    for name in DOCUMENT_WRITING_COMMANDS:
        setattr(viewer_class, name, refused_during_operation(getattr(viewer_class, name)))
