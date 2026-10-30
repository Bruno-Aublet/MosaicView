"""Conversion en CBZ qui supprime sa source : écrire, relire, puis seulement
supprimer l'original. Seule implémentation de cette séquence dans
l'application (lots, import de métadonnées, panneau, Bibliothèque).

Deux formes, selon le moment où le CBZ prend son nom :

- write_cbz (lots, import de métadonnées) : le CBZ est écrit dans un
  temporaire du dossier de destination, relu entièrement, puis prend son
  nom ; delete_source supprime ensuite la source, et refuse de le faire si
  ce CBZ n'a pas été relu avant de prendre son nom.
- reread_then_delete_source (panneau, Bibliothèque) : le CBZ porte déjà son
  nom quand l'utilisateur répond « Supprimer l'original ? » ; il est relu
  par ce nom juste avant la suppression. Un échec n'y supprime rien.

Un CBZ relu après avoir pris un nom qu'il vient de créer ne doit jamais
être supprimé par ce nom s'il est abîmé : un autre programme ou un
enregistrement depuis un panneau peut y avoir écrit entre-temps (la
réservation de batch_claims ne protège que des autres lots). D'où la
relecture du temporaire dans write_cbz.

Module sans Qt : les fonctions de utils.py sont importées à l'appel."""
import contextlib
import os
import zipfile


class CbzWriter:
    """ZipFile en écriture rendu par write_cbz.

    before_write() est appelé avant chaque écriture : il lève l'exception qui
    arrête l'écriture (fermeture de l'application, annulation d'un lot ou d'un
    import). written_files : fichiers écrits, dossiers exclus.
    expected_count : nombre de fichiers attendu au contrôle du CBZ, à poser
    dans le bloc quand l'appelant le connaît (pages de la source) ; à défaut,
    written_files. source_may_be_deleted ne devient vrai qu'après la relecture
    et le renommage réussis (delete_source)."""

    def __init__(self, zf, before_write=None):
        self._zf = zf
        self._before_write = before_write
        self.written_files = 0
        self.expected_count = None
        self.source_may_be_deleted = False

    def writestr(self, zinfo_or_arcname, *args, **kwargs):
        if self._before_write is not None:
            self._before_write()
        result = self._zf.writestr(zinfo_or_arcname, *args, **kwargs)
        name = getattr(zinfo_or_arcname, "filename", zinfo_or_arcname)
        if not name.endswith("/"):
            self.written_files += 1
        return result

    def write(self, filename, *args, **kwargs):
        if self._before_write is not None:
            self._before_write()
        result = self._zf.write(filename, *args, **kwargs)
        if not os.path.isdir(filename):
            self.written_files += 1
        return result

    def __getattr__(self, name):
        return getattr(self._zf, name)

    @property
    def comment(self):
        return self._zf.comment

    @comment.setter
    def comment(self, value):
        # Sans ce setter, l'affectation créerait un attribut de l'enveloppe et
        # le commentaire d'archive ne serait jamais écrit dans le ZIP.
        self._zf.comment = value

    def count_to_check(self):
        return self.written_files if self.expected_count is None else self.expected_count


@contextlib.contextmanager
def write_cbz(path, *, reread=True, check_structure=False, before_write=None, **zip_kwargs):
    """Écrit le CBZ `path` à travers le CbzWriter rendu.

    Le ZIP est écrit dans un temporaire du dossier de `path` (même volume),
    qui ne prend le nom `path` (replace_file_retrying) qu'une fois fermé et
    contrôlé : contrôle rapide de la structure si check_structure
    (check_written_cbz_structure, réécriture d'un CBZ sur place), relecture
    complète si reread (verify_written_cbz, CRC de chaque fichier, obligatoire
    avant de supprimer une source), puis fsync. En cas d'échec — exception du
    bloc, interruption, disque plein à la fermeture, CBZ qui ne se relit pas
    — le temporaire est supprimé, sauf s'il est devenu la seule copie d'un
    fichier (is_kept_temp), et `path` n'est jamais créé ni modifié. Un crash
    du processus peut au pire laisser un `.~mosaicview_*.tmp`, supprimé à la
    prochaine écriture dans ce dossier (make_temp_path).

    Un CBZ incomplet se signale en levant une exception dans le bloc : il ne
    prend alors jamais son nom. Le constater après le bloc obligerait à le
    supprimer par ce nom, qui peut entre-temps désigner le fichier d'un autre
    traitement."""
    from modules.qt.utils import (check_written_cbz_structure, fsync_file,
                                  is_kept_temp, make_temp_path, release_temp_path,
                                  replace_file_retrying, verify_written_cbz)
    tmp_path = make_temp_path(os.path.dirname(os.path.abspath(path)))
    try:
        with zipfile.ZipFile(tmp_path, 'w', **zip_kwargs) as zf:
            writer = CbzWriter(zf, before_write)
            yield writer
        expected = writer.count_to_check()
        if check_structure:
            check_written_cbz_structure(tmp_path, expected, shown_path=path)
        if reread:
            verify_written_cbz(tmp_path, expected, shown_path=path)
        fsync_file(tmp_path)
        replace_file_retrying(tmp_path, path)
    except BaseException:
        if not is_kept_temp(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise
    finally:
        release_temp_path(tmp_path)
    writer.source_may_be_deleted = reread


def delete_source(written, source, *, permanent=False, source_signature=None):
    """Supprime `source`, convertie en CBZ par `written` (CbzWriter d'un
    write_cbz terminé) : définitivement si permanent, sinon à la Corbeille
    (utils.send_to_trash). Refusé (RuntimeError, défaut du programme) si ce
    CBZ n'a pas été relu avant de prendre son nom. Une erreur de suppression
    remonte telle quelle : la source reste en place, le CBZ aussi.

    source_signature : utils.file_signature de la source avant la lecture de
    ses pages (check_source_unchanged) ; si elle a changé depuis,
    SourceChangedError et rien n'est supprimé."""
    if not getattr(written, "source_may_be_deleted", False):
        raise RuntimeError("delete_source: the CBZ was not reread before taking its name")
    if source_signature is not None:
        check_source_unchanged(source, source_signature)
    _remove_source(source, permanent)


class SourceChangedError(Exception):
    """La source `path` d'une conversion a été réécrite (date ou taille) par
    un autre programme pendant la lecture de ses pages : une archive RAR ou
    7z est rouverte à chaque page, et le CBZ pourrait mêler les pages de deux
    versions. La source n'est pas supprimée. Texte traduit à chaque str()."""

    expected_error = True  # cas normal, signalé dans le journal du lot : jamais écrit dans le journal des erreurs

    def __init__(self, path):
        super().__init__(path)
        self.path = path

    def __str__(self):
        from modules.qt.localization import _
        return _("messages.errors.batch_source_changed", name=os.path.basename(self.path))


def check_source_unchanged(source, signature):
    """Lève SourceChangedError si `source` n'a plus la signature
    (utils.file_signature) relevée avant la lecture de ses pages, ou a
    disparu. Appelée dans le bloc de write_cbz après la copie des pages — le
    CBZ ne prend alors jamais son nom — puis par delete_source, pour une
    réécriture faite pendant la relecture du CBZ."""
    from modules.qt.utils import file_signature
    if file_signature(source) != signature:
        raise SourceChangedError(source)


class ConvertedCbzChangedError(Exception):
    """Le CBZ `path` a été réécrit (date ou taille) entre son écriture et la
    réponse « Supprimer l'original ? » : il n'est peut-être plus la copie de
    `source`, qui n'est donc pas supprimée. Texte traduit à chaque str(),
    comme utils.FileInUseError."""

    expected_error = True  # cas normal, signalé à l'utilisateur : jamais écrit dans le journal des erreurs

    def __init__(self, path, source):
        super().__init__(path)
        self.path = path
        self.source = source

    def __str__(self):
        from modules.qt.localization import _
        return _("messages.errors.converted_cbz_changed",
                 name=os.path.basename(self.path), source=os.path.basename(self.source))


def reread_then_delete_source(cbz_path, expected_count, source, *, permanent=False,
                              written_signature=None):
    """CBZ `cbz_path` déjà écrit sous son nom (panneau, Bibliothèque, après
    la réponse « Supprimer l'original ? ») : relu entièrement
    (verify_written_cbz, nombre de fichiers expected_count), puis `source`
    supprimée comme par delete_source. Rend False sans rien faire si la
    source n'existe plus, True une fois supprimée. Un CBZ qui ne se relit pas
    lève WrittenFileCheckError : rien n'est supprimé, ni la source ni le CBZ.

    written_signature : utils.file_signature du CBZ juste après son
    écriture. La réponse de l'utilisateur pouvant se faire attendre, un autre
    programme, l'autre panneau ou la Bibliothèque a pu réécrire ce fichier
    entre-temps : s'il a changé, ConvertedCbzChangedError, rien n'est relu ni
    supprimé. None : pas de contrôle (signature inconnue)."""
    if not os.path.exists(source):
        return False
    if written_signature is not None:
        from modules.qt.utils import file_signature
        if file_signature(cbz_path) != written_signature:
            raise ConvertedCbzChangedError(cbz_path, source)
    from modules.qt.utils import verify_written_cbz
    verify_written_cbz(cbz_path, expected_count)
    _remove_source(source, permanent)
    return True


def _remove_source(source, permanent):
    """Appelée seulement par delete_source et reread_then_delete_source,
    après la relecture (garde-fou tests/test_check_atomic_file_writes.py)."""
    if permanent:
        os.remove(source)
    else:
        from modules.qt.utils import send_to_trash
        send_to_trash(source)
