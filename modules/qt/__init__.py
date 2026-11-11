import sys

# PyMuPDF charge tout seul, dès son import, son extension d'analyse de mise
# en page par IA (pymupdf.layout : ONNX, numpy), installée d'office avec lui.
# MosaicView ne s'en sert jamais, et elle ajoute plus de 3 s au démarrage de
# chaque process PDF (ouverture, ajout, lots, Bibliothèque), pendant lesquelles
# rien ne s'affiche. Marquée absente ici, avant tout import de PyMuPDF : tout
# process de l'application passe par ce paquet, process PDF compris (ils
# importent pdf_loading_qt ou pdf_probe). PyMuPDF s'en passe sans erreur ;
# l'exe l'exclut déjà (fichiers .spec).
sys.modules.setdefault("pymupdf.layout", None)
