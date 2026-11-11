"""
file_picker.py — Règles de la fenêtre de choix d'un fichier ou d'un dossier,
sans Qt (file_picker_qt.py les applique).

- point de départ de la fenêtre d'après le chemin donné par l'appelant
  (dossier, fichier existant ou non, nom seul) ;
- chemin tapé par l'utilisateur, absolu ou relatif au dossier affiché ;
- noms de plusieurs fichiers dans un même champ (« "a.cbz" "b.cbz" ») ;
- motifs d'un filtre de types et extension ajoutée à l'enregistrement ;
- dossier parent, jusqu'à la liste des lecteurs (chemin vide) ;
- historique Précédent / Suivant ;
- nom libre pour un nouveau dossier.
"""

import fnmatch
import os
import re
import time

# Dossier « Ce PC » : la liste des lecteurs
COMPUTER = ""

_DRIVE_ONLY = re.compile(r"^[A-Za-z]:$")


def matches(name, patterns):
    """True si `name` répond à l'un des motifs (« *.cbz », « * »), sans tenir
    compte de la casse, comme Windows."""
    lower = name.lower()
    return any(fnmatch.fnmatchcase(lower, p.lower()) for p in patterns)


def default_extension(patterns):
    """Extension ajoutée à l'enregistrement : celle du premier motif de la
    forme « *.ext » (sans joker dans l'extension), None pour « * »."""
    for pattern in patterns:
        if pattern.startswith("*.") and not any(c in pattern[2:] for c in "*?["):
            return pattern[1:]
    return None


def with_extension(name, patterns):
    """Nom enregistré : l'extension du filtre est ajoutée si le nom ne répond
    à aucun de ses motifs (« Aaaa » → « Aaaa.cbz », « Aaaa.txt » →
    « Aaaa.txt.cbz »), comme le faisait la boîte de Windows."""
    ext = default_extension(patterns)
    if ext is None or matches(name, patterns):
        return name
    return name + ext


def split_names(text):
    """Noms du champ en ouverture de plusieurs fichiers : chaque nom entre
    guillemets, ou le texte entier s'il n'y en a aucun."""
    if '"' in text:
        return [n for n in re.findall(r'"([^"]*)"', text) if n.strip()]
    text = text.strip()
    return [text] if text else []


def join_names(names):
    """Inverse de split_names : un nom seul tel quel, plusieurs entre guillemets."""
    if len(names) == 1:
        return names[0]
    return " ".join(f'"{n}"' for n in names)


def resolve(current_dir, typed):
    """Chemin désigné par le texte tapé : absolu, ou relatif au dossier
    affiché. Les variables d'environnement (%USERPROFILE%) et « ~ » sont
    développées ; « C: » désigne la racine du lecteur. "" si rien n'est tapé."""
    typed = typed.strip()
    if not typed:
        return ""
    typed = os.path.expanduser(os.path.expandvars(typed))
    if _DRIVE_ONLY.match(typed):
        typed += "\\"
    if not os.path.isabs(typed) and not os.path.splitdrive(typed)[0]:
        if not current_dir:
            return os.path.normpath(typed)
        typed = os.path.join(current_dir, typed)
    return os.path.normpath(typed)


def existing_ancestor(path):
    """`path` s'il est un dossier existant, sinon le plus proche de ses
    dossiers parents qui existe ; "" si aucun (lecteur absent)."""
    if not path:
        return ""
    path = os.path.normpath(path)
    while not os.path.isdir(path):
        parent = os.path.dirname(path)
        if parent == path or not parent:
            return ""
        path = parent
    return path


def start_location(start, home, directory_only=False):
    """(dossier affiché, nom pré-rempli) à l'ouverture de la fenêtre.

    start : dossier, fichier (existant ou non : nom proposé à
    l'enregistrement) ou vide. directory_only : choix d'un dossier, `start`
    est un dossier même s'il n'existe plus (on remonte alors à son plus
    proche parent existant), jamais un nom à pré-remplir. Dossier introuvable :
    `home`."""
    if not start:
        return home, ""
    start = os.path.normpath(start)
    if directory_only or os.path.isdir(start):
        return existing_ancestor(start) or home, ""
    folder, name = os.path.split(start)
    return existing_ancestor(folder) or home, name


def parent_folder(path):
    """Dossier parent de `path` ; la racine d'un lecteur ou d'un partage
    réseau mène à la liste des lecteurs (COMPUTER), qui n'a pas de parent
    (None)."""
    if not path:
        return None
    path = os.path.normpath(path)
    parent = os.path.dirname(path)
    if parent == path or not parent:
        return COMPUTER
    return parent


def open_file_under(path, open_files):
    """Fichier de `open_files` (fichiers ouverts dans l'application : comics
    des panneaux, base de la Bibliothèque) qui est `path` lui-même ou se
    trouve dans le dossier `path`, None sinon (comparaison sans casse, comme
    Windows). Le renommer ou le supprimer laisserait l'application sur un
    fichier qui n'existe plus sous ce nom. L'enregistrer par-dessus reste
    permis : ce n'est pas cette règle."""
    root = os.path.normcase(os.path.abspath(path))
    prefix = root.rstrip("\\/") + os.sep
    for opened in open_files:
        key = os.path.normcase(os.path.abspath(opened))
        if key == root or key.startswith(prefix):
            return opened
    return None


def unique_folder_name(existing_names, base):
    """`base` s'il est libre dans le dossier, sinon « base (2) », « base (3) »…
    (comparaison sans casse, comme Windows)."""
    taken = {n.lower() for n in existing_names}
    if base.lower() not in taken:
        return base
    n = 2
    while f"{base} ({n})".lower() in taken:
        n += 1
    return f"{base} ({n})"


def drive_display(root, label):
    """Texte d'un lecteur dans le volet de gauche : « Nom (C:) » comme
    l'Explorateur, la racine seule (« C:\\ ») sans nom. Caractères de
    contrôle du nom retirés (il vient du disque)."""
    label = "".join(c for c in (label or "") if ord(c) >= 32).strip()
    if not label:
        return root
    return f"{label} ({root.rstrip(chr(92) + '/')})"


_DRIVE_REMOTE, _DRIVE_CDROM = 4, 5


def drive_label(root):
    """Nom d'un lecteur (« C:\\ ») : nom du volume, ou chemin du partage pour
    un lecteur réseau. "" s'il n'y en a pas ou s'il est illisible. Le nom
    d'un lecteur réseau vient de la table des connexions de Windows (sans
    contacter le serveur, qui peut être injoignable) ; un lecteur de CD/DVD
    n'est pas interrogé (un disque à faire tourner bloquerait la fenêtre)."""
    try:
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        drive_type = kernel32.GetDriveTypeW(ctypes.c_wchar_p(root))
        if drive_type == _DRIVE_CDROM:
            return ""
        if drive_type == _DRIVE_REMOTE:
            mpr = ctypes.WinDLL("mpr")
            buffer = ctypes.create_unicode_buffer(1024)
            size = ctypes.c_ulong(len(buffer))
            if mpr.WNetGetConnectionW(ctypes.c_wchar_p(root.rstrip("\\/")), buffer,
                                      ctypes.byref(size)) == 0:
                return buffer.value
            return ""
        name = ctypes.create_unicode_buffer(261)
        if kernel32.GetVolumeInformationW(ctypes.c_wchar_p(root), name, len(name),
                                          None, None, None, None, 0):
            return name.value
    except (OSError, AttributeError, ValueError):
        pass  # repli : lecteur affiché par sa seule lettre
    return ""


def format_date(timestamp):
    """Date de modification affichée : « AAAA-MM-JJ HH:MM », lisible dans
    toutes les langues."""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(timestamp))


class NavigationHistory:
    """Dossiers visités, pour Précédent et Suivant."""

    def __init__(self):
        self.current = None
        self._back = []
        self._forward = []

    def visit(self, path):
        """Nouveau dossier affiché : la liste Suivant est vidée."""
        if path == self.current:
            return
        if self.current is not None:
            self._back.append(self.current)
        self._forward.clear()
        self.current = path

    def can_go_back(self):
        return bool(self._back)

    def can_go_forward(self):
        return bool(self._forward)

    def back(self):
        """Dossier précédent (devenu le courant), None s'il n'y en a pas."""
        if not self._back:
            return None
        self._forward.append(self.current)
        self.current = self._back.pop()
        return self.current

    def forward(self):
        """Dossier suivant (devenu le courant), None s'il n'y en a pas."""
        if not self._forward:
            return None
        self._back.append(self.current)
        self.current = self._forward.pop()
        return self.current
