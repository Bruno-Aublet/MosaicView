"""
modules/qt/pdf_probe.py — Accès courts à un PDF dans un process séparé.

Le nombre de pages d'un PDF au scan de la Bibliothèque, la couverture de son
aperçu, la couverture d'un PDF dans l'import de métadonnées en lot et la copie
déverrouillée d'un PDF sont faits par PyMuPDF dans un process à part : un PDF
malformé qui fait planter MuPDF en code natif ne tue que ce process, pas
l'application avec le travail non enregistré des deux panneaux.

Chaque PdfProbe a son propre process (jamais partagé entre appelants, comme
pour le chargement des PDF, skill pdf-loading), lancé à la première demande et
gardé pour les suivantes : un scan lit des centaines de PDF à la suite. Un
process qui plante ou ne répond pas dans le délai est tué, la demande échoue,
et le suivant est lancé à la demande d'après. Sans Qt.
"""

import multiprocessing
import threading
import time
import weakref

from modules.qt.error_log import logged_child_process

# Délai de réponse d'une demande, au-delà duquel le process est tué
REQUEST_TIMEOUT = 30.0

# Issue d'une demande (PdfProbe.call)
OK = 'ok'            # résultat rendu
ERROR = 'error'      # PyMuPDF a levé une exception (message rendu)
CRASHED = 'crashed'  # process mort pendant la demande
TIMEOUT = 'timeout'  # pas de réponse dans le délai
CLOSED = 'closed'    # PdfProbe arrêté (shutdown)

_probes = weakref.WeakSet()


@logged_child_process
def _probe_process(in_q, out_send):
    """Boucle du process. Commandes :
    ('count', chemin) -> nombre de pages ;
    ('cover', chemin) -> (largeur, hauteur, octets RGB) de la page 0 ;
    ('cover_jpeg', chemin, dpi) -> JPEG de la page 0, b'' si le PDF n'a aucune page ;
    ('unlock', chemin, destination) -> None, copie écrite sans chiffrement ;
    ('quit',) arrête le process.
    Réponse ('ok', résultat), ou ('error', message) en cas d'exception."""
    from modules.qt.error_log import start_child_crash_log
    start_child_crash_log()  # pile d'un plantage de MuPDF
    import queue
    import pymupdf as fitz

    parent = multiprocessing.parent_process()
    while True:
        try:
            msg = in_q.get(timeout=1)
        except queue.Empty:
            # Application terminée sans avoir arrêté ce process
            if parent is not None and not parent.is_alive():
                return
            continue
        kind = msg[0]
        if kind == 'quit':
            return
        try:
            if kind == 'count':
                doc = fitz.open(msg[1])
                try:
                    result = doc.page_count
                finally:
                    doc.close()
            elif kind == 'cover':
                doc = fitz.open(msg[1])
                try:
                    pix = doc[0].get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False)
                    result = (pix.width, pix.height, bytes(pix.samples))
                finally:
                    doc.close()
            elif kind == 'cover_jpeg':
                doc = fitz.open(msg[1])
                try:
                    if doc.is_closed or len(doc) == 0:
                        result = b''
                    else:
                        result = doc[0].get_pixmap(dpi=msg[2]).tobytes("jpeg")
                finally:
                    doc.close()
            elif kind == 'unlock':
                doc = fitz.open(msg[1])
                try:
                    doc.authenticate("")
                    doc.save(msg[2], encryption=fitz.PDF_ENCRYPT_NONE)
                finally:
                    doc.close()
                result = None
            else:
                raise ValueError(f"unknown request: {kind}")
            out_send.send((OK, result))
        except Exception as e:
            # La pile d'une erreur inattendue ne passe pas par le canal
            from modules.qt.error_log import log_if_unexpected
            log_if_unexpected(e, "error_log.entry.pdf_error")
            try:
                out_send.send((ERROR, str(e) or type(e).__name__))
            except Exception:
                return


class PdfProbe:
    """Process d'accès aux PDF propre à un appelant. Une demande à la fois
    (verrou) ; close() et shutdown() peuvent être appelées depuis n'importe
    quel thread, même pendant une demande, qui échoue alors aussitôt."""

    def __init__(self, timeout=REQUEST_TIMEOUT):
        self._timeout = timeout
        self._lock = threading.Lock()
        self._proc = None
        self._in_q = None
        self._out_recv = None
        self._closed = False
        # Process arrêté par close() : terminate() n'est pas immédiat, il peut
        # encore passer pour vivant à la demande suivante
        self._replace = False
        _probes.add(self)

    def page_count(self, path):
        """Nombre de pages du PDF, ou None en cas d'échec."""
        status, result = self.call(('count', path))
        return result if status == OK else None

    def cover_rgb(self, path):
        """(largeur, hauteur, octets RGB) de la première page, ou None."""
        status, result = self.call(('cover', path))
        return result if status == OK else None

    def call(self, request, timeout=REQUEST_TIMEOUT):
        """Envoie `request` (tuple, voir _probe_process) et rend (issue,
        résultat) : (OK, résultat), (ERROR, message), ou (CRASHED | TIMEOUT |
        CLOSED, None). timeout=None : pas de délai (écriture d'un gros PDF),
        la demande ne finit que par la réponse, la mort du process ou
        shutdown()."""
        with self._lock:
            if self._closed:
                return CLOSED, None
            status = CRASHED
            try:
                self._ensure_process()
                self._in_q.put(request)
                deadline = None if timeout is None else time.monotonic() + timeout
                while True:
                    # shutdown() pendant le lancement du process, qu'il n'a
                    # pas pu arrêter
                    if self._closed:
                        status = CLOSED
                        break
                    if self._out_recv.poll(0.05):
                        return self._out_recv.recv()
                    if not self._proc.is_alive():
                        status = CLOSED if self._closed else CRASHED
                        break
                    if deadline is not None and time.monotonic() >= deadline:
                        status = TIMEOUT
                        break
            except Exception:
                if self._closed:
                    status = CLOSED
            # Process planté, arrêté, ou sans réponse dans le délai
            self._discard_process()
            return status, None

    def close(self):
        """Arrête le process. Une demande en cours échoue ; la suivante
        relance un process (close() ne rend pas l'objet inutilisable)."""
        self._replace = True
        proc = self._proc
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass  # sans conséquence : process déjà terminé

    def shutdown(self):
        """Arrêt définitif : plus aucune demande n'est traitée."""
        self._closed = True
        self.close()

    def _ensure_process(self):
        if self._proc is not None and self._proc.is_alive() and not self._replace:
            return
        self._replace = False
        self._discard_process()
        from modules.qt.error_log import pass_titles_to_children
        pass_titles_to_children()  # titres traduits de ses entrées du journal
        ctx = multiprocessing.get_context('spawn')
        in_q = ctx.Queue()
        out_recv, out_send = ctx.Pipe(duplex=False)
        proc = ctx.Process(target=_probe_process, args=(in_q, out_send), daemon=True)
        proc.start()
        # Seul le process enfant écrit dans le pipe : sans cette fermeture, sa
        # mort ne serait pas vue comme une fin de pipe
        out_send.close()
        self._proc, self._in_q, self._out_recv = proc, in_q, out_recv

    def _discard_process(self):
        proc, in_q, out_recv = self._proc, self._in_q, self._out_recv
        self._proc = self._in_q = self._out_recv = None
        if proc is not None:
            try:
                if proc.is_alive():
                    proc.terminate()
                proc.join(timeout=2)
            except Exception:
                pass  # sans conséquence : process abandonné, un nouveau sera lancé si besoin
        for obj in (out_recv, in_q):
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass  # sans conséquence : canal déjà fermé


def shutdown_pdf_probes():
    """Fermeture de l'application : arrête tous les process d'accès aux PDF,
    ce qui libère aussitôt le thread qui attend leur réponse."""
    for probe in list(_probes):
        probe.shutdown()
