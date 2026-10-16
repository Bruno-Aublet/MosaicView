"""
modules/qt/pymupdf_lock.py — Verrou de PyMuPDF dans le process de l'application.

PyMuPDF (MuPDF) ne peut pas être utilisé depuis plusieurs threads en même
temps : deux appels simultanés peuvent faire planter tout le process, et avec
lui le travail non enregistré des deux panneaux. Les panneaux et les lots PDF
travaillent dans un process dédié (pdf_loading_qt._spawn_pdf_process), mais
quelques lectures courtes restent dans le process de l'application, chacune
dans son thread : nombre de pages au scan de la Bibliothèque, aperçu de la
Bibliothèque, couverture d'un PDF dans l'import des métadonnées en lot,
copie déverrouillée d'un PDF. Tout appel à PyMuPDF fait dans ce process se fait
sous PYMUPDF_LOCK (garde-fou : tests/test_check_pymupdf_lock.py). Sans Qt.
"""

import threading

PYMUPDF_LOCK = threading.RLock()
