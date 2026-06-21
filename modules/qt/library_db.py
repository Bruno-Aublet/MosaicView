"""
modules/qt/library_db.py — Moteur SQLite de la bibliothèque MosaicView

Gère :
  - Création / ouverture d'une DB bibliothèque (.db)
  - Structure des tables (comics, directories)
  - Scan incrémental (nouveaux / modifiés / supprimés)
  - Lecture/écriture is_read
  - Sauvegarde automatique .db.old avant chaque écriture
"""

import os
import shutil
import sqlite3
import zipfile
import tarfile
import datetime

from modules.qt.utils import safe_join

try:
    import rarfile
except ImportError:
    rarfile = None

# Extensions supportées
_ARCHIVE_EXTS   = {'.cbz', '.cbr', '.cb7', '.cbt', '.zip'}
_NO_COMICINFO   = {'.pdf', '.jpg', '.jpeg', '.png', '.gif', '.tiff', '.tif',
                   '.bmp', '.webp', '.epub', '.avif', '.heic', '.heif'}
_ALL_EXTS       = _ARCHIVE_EXTS | _NO_COMICINFO

# Champs ComicInfo stockés dans la DB (correspond à parse_comic_info_xml)
_COMICINFO_FIELDS = [
    'title', 'series', 'number', 'volume', 'summary', 'writer',
    'penciller', 'inker', 'colorist', 'letterer', 'cover_artist', 'editor',
    'publisher', 'imprint', 'genre', 'web', 'page_count', 'language_iso',
    'format', 'black_and_white', 'manga', 'year', 'month', 'day', 'notes',
    'characters', 'teams', 'locations', 'story_arc', 'story_arc_number',
    'series_group', 'count', 'alternate_series', 'alternate_number',
    'alternate_count', 'age_rating', 'series_complete', 'translator',
    'tags', 'scan_information', 'community_rating', 'review', 'gtin',
]

_CREATE_COMICS = """
CREATE TABLE IF NOT EXISTS comics (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    relative_path    TEXT NOT NULL,
    filename         TEXT NOT NULL,
    file_extension   TEXT NOT NULL,
    file_size        INTEGER,
    file_modified_at TEXT,
    indexed_at       TEXT,
    has_comicinfo    INTEGER NOT NULL DEFAULT 0,
    can_have_comicinfo INTEGER NOT NULL DEFAULT 1,
    is_read          INTEGER NOT NULL DEFAULT 0,
    page_count       INTEGER,
    title            TEXT,
    series           TEXT,
    number           TEXT,
    volume           TEXT,
    summary          TEXT,
    writer           TEXT,
    penciller        TEXT,
    inker            TEXT,
    colorist         TEXT,
    letterer         TEXT,
    cover_artist     TEXT,
    editor           TEXT,
    publisher        TEXT,
    imprint          TEXT,
    genre            TEXT,
    web              TEXT,
    language_iso     TEXT,
    format           TEXT,
    black_and_white  TEXT,
    manga            TEXT,
    year             TEXT,
    month            TEXT,
    day              TEXT,
    notes            TEXT,
    characters       TEXT,
    teams            TEXT,
    locations        TEXT,
    story_arc        TEXT,
    story_arc_number TEXT,
    series_group     TEXT,
    count            TEXT,
    alternate_series TEXT,
    alternate_number TEXT,
    alternate_count  TEXT,
    age_rating       TEXT,
    series_complete  TEXT,
    translator       TEXT,
    tags             TEXT,
    scan_information TEXT,
    community_rating TEXT,
    review           TEXT,
    gtin             TEXT,
    UNIQUE(relative_path)
)
"""

_CREATE_DIRECTORIES = """
CREATE TABLE IF NOT EXISTS directories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    path       TEXT NOT NULL UNIQUE,
    is_master  INTEGER NOT NULL DEFAULT 0
)
"""

_CREATE_META = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
)
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso():
    return datetime.datetime.now().isoformat(timespec='seconds')


def _date_bound_before(v: str) -> str:
    """Borne inférieure pour 'avant' : retourne v tel quel (comparaison ISO prefix suffit)."""
    return v.strip()


def _date_bound_after(v: str) -> str:
    """Borne pour 'après' : incrémente la partie pertinente selon la précision saisie.
    '2024'      → '2025'
    '2024-03'   → '2024-04'
    '2024-12'   → '2025-01'
    '2024-03-15'→ '2024-03-16'
    '2024-03-31'→ '2024-04-01'  (approximation : on laisse SQLite gérer les débordements via tri lexico)
    """
    v = v.strip()
    parts = v.split('-')
    try:
        if len(parts) == 1:
            return str(int(parts[0]) + 1)
        elif len(parts) == 2:
            y, m = int(parts[0]), int(parts[1])
            if m == 12:
                return f"{y + 1:04d}-01"
            return f"{y:04d}-{m + 1:02d}"
        else:
            y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
            # Utilise datetime pour incrémenter proprement le jour
            dt = datetime.date(y, m, d) + datetime.timedelta(days=1)
            return dt.isoformat()
    except (ValueError, TypeError):
        return v


def _ext(filename):
    return os.path.splitext(filename)[1].lower()


def _can_have_comicinfo(filepath):
    return _ext(filepath) in _ARCHIVE_EXTS


def _has_comicinfo_in_archive(filepath):
    """Retourne True si l'archive contient un ComicInfo.xml."""
    ext = _ext(filepath)
    try:
        if ext in ('.cbz', '.zip'):
            with zipfile.ZipFile(filepath, 'r') as zf:
                names = [n.lower() for n in zf.namelist()]
                return 'comicinfo.xml' in names
        elif ext == '.cbt':
            with tarfile.open(filepath, 'r:*') as tf:
                names = [m.name.lower() for m in tf.getmembers()]
                return 'comicinfo.xml' in names
        elif ext == '.cbr' and rarfile:
            with rarfile.RarFile(filepath, 'r') as rf:
                names = [n.lower() for n in rf.namelist()]
                return 'comicinfo.xml' in names
    except Exception:
        pass
    return False


def _read_comicinfo_from_archive(filepath):
    """Lit et retourne le contenu bytes de ComicInfo.xml, ou None."""
    ext = _ext(filepath)
    try:
        if ext in ('.cbz', '.zip'):
            with zipfile.ZipFile(filepath, 'r') as zf:
                for name in zf.namelist():
                    if name.lower() == 'comicinfo.xml':
                        return zf.read(name)
        elif ext == '.cbt':
            with tarfile.open(filepath, 'r:*') as tf:
                for m in tf.getmembers():
                    if m.name.lower() == 'comicinfo.xml':
                        return tf.extractfile(m).read()
        elif ext == '.cbr' and rarfile:
            with rarfile.RarFile(filepath, 'r') as rf:
                for name in rf.namelist():
                    if name.lower() == 'comicinfo.xml':
                        return rf.read(name)
    except Exception:
        pass
    return None


def _count_pages(filepath):
    """Retourne le nombre de pages (images dans archive, pages PDF, etc.) ou None."""
    ext = _ext(filepath)
    _IMG = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tiff', '.tif', '.avif'}
    try:
        if ext in ('.cbz', '.zip'):
            with zipfile.ZipFile(filepath, 'r') as zf:
                return sum(1 for n in zf.namelist()
                           if os.path.splitext(n)[1].lower() in _IMG)
        elif ext == '.cbt':
            with tarfile.open(filepath, 'r:*') as tf:
                return sum(1 for m in tf.getmembers()
                           if os.path.splitext(m.name)[1].lower() in _IMG)
        elif ext == '.cbr' and rarfile:
            with rarfile.RarFile(filepath, 'r') as rf:
                return sum(1 for n in rf.namelist()
                           if os.path.splitext(n)[1].lower() in _IMG)
        elif ext == '.pdf':
            import fitz
            doc = fitz.open(filepath)
            n = doc.page_count
            doc.close()
            return n
        elif ext in ('.tiff', '.tif'):
            from PIL import Image
            with Image.open(filepath) as img:
                try:
                    n = 0
                    while True:
                        n += 1
                        img.seek(n)
                except EOFError:
                    return n
        elif ext in _IMG:
            return 1
    except Exception:
        pass
    return None


def _file_mtime_iso(filepath):
    try:
        t = os.path.getmtime(filepath)
        return datetime.datetime.fromtimestamp(t).isoformat(timespec='seconds')
    except Exception:
        return None


def _backup(db_path):
    """Renomme db_path → db_path.old (écrase l'ancienne sauvegarde)."""
    old = db_path + '.old'
    if os.path.exists(db_path):
        shutil.copy2(db_path, old)


# ── LibraryDB ─────────────────────────────────────────────────────────────────

class LibraryDB:
    """
    Interface haut niveau vers une base de données bibliothèque MosaicView.

    Usage :
        db = LibraryDB.create('/chemin/marvel.db', '/répertoire/maître')
        db = LibraryDB.open('/chemin/marvel.db')
        stats = db.scan(progress_callback=lambda msg: ...)
        db.set_read([id1, id2], True)
        rows = db.search([{'field': 'series', 'op': 'contains', 'value': 'Spider'}])
        db.close()
    """

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    # ── Cycle de vie ──────────────────────────────────────────────────────────

    @classmethod
    def create(cls, db_path: str, master_dir: str) -> 'LibraryDB':
        """Crée une nouvelle DB à db_path avec master_dir comme répertoire maître."""
        if os.path.exists(db_path):
            _backup(db_path)
            os.remove(db_path)
        db = cls(db_path)
        db._connect()
        db._init_schema()
        db._conn.execute(
            "INSERT OR REPLACE INTO directories (path, is_master) VALUES (?, 1)",
            (os.path.normpath(master_dir),)
        )
        db._conn.commit()
        return db

    @classmethod
    def open(cls, db_path: str) -> 'LibraryDB':
        """Ouvre une DB existante."""
        db = cls(db_path)
        db._connect()
        db._init_schema()
        return db

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def reopen(self):
        """Ferme et rouvre la connexion pour voir les données écrites par d'autres connexions."""
        self.close()
        self._connect()

    @property
    def db_path(self):
        return self._db_path

    @property
    def name(self):
        return os.path.splitext(os.path.basename(self._db_path))[0]

    # ── Schéma ────────────────────────────────────────────────────────────────

    def _connect(self):
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row

    def _init_schema(self):
        self._conn.execute(_CREATE_COMICS)
        self._conn.execute(_CREATE_DIRECTORIES)
        self._conn.execute(_CREATE_META)
        self._conn.commit()

    # ── Répertoires ───────────────────────────────────────────────────────────

    def get_master_dir(self) -> str | None:
        row = self._conn.execute(
            "SELECT path FROM directories WHERE is_master=1 LIMIT 1"
        ).fetchone()
        return row['path'] if row else None

    def set_master_dir(self, new_path: str):
        _backup(self._db_path)
        self._conn.execute(
            "UPDATE directories SET path=? WHERE is_master=1", (os.path.normpath(new_path),)
        )
        self._conn.commit()

    def get_additional_dirs(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT path FROM directories WHERE is_master=0"
        ).fetchall()
        return [r['path'] for r in rows]

    def add_directory(self, path: str):
        _backup(self._db_path)
        self._conn.execute(
            "INSERT OR IGNORE INTO directories (path, is_master) VALUES (?, 0)",
            (os.path.normpath(path),)
        )
        self._conn.commit()

    def get_all_dirs(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT path FROM directories ORDER BY is_master DESC"
        ).fetchall()
        return [r['path'] for r in rows]

    # ── Renommage DB ──────────────────────────────────────────────────────────

    def rename(self, new_name: str) -> str:
        """Renomme le fichier .db (même répertoire). Retourne le nouveau chemin."""
        dir_ = os.path.dirname(self._db_path)
        new_path = os.path.join(dir_, new_name + '.mvdb')
        self.close()
        _backup(self._db_path)
        os.rename(self._db_path, new_path)
        self._db_path = new_path
        self._connect()
        return new_path

    # ── Scan ──────────────────────────────────────────────────────────────────

    def scan(self, progress_callback=None, stop_event=None) -> dict:
        """
        Scan incrémental de tous les répertoires.

        progress_callback(msg: str) : appelé pour chaque événement.
        stop_event : threading.Event — si set(), le scan s'arrête proprement.

        Retourne {'new': int, 'updated': int, 'deleted': int}.
        """
        dirs = self.get_all_dirs()
        master = self.get_master_dir()

        # Collecte tous les fichiers sur disque
        disk_files: dict[str, str] = {}  # relative_path → absolute_path
        for d in dirs:
            if not os.path.isdir(d):
                continue
            for root, _, files in os.walk(d):
                for fname in files:
                    if _ext(fname) not in _ALL_EXTS:
                        continue
                    abs_path = os.path.join(root, fname)
                    if master:
                        try:
                            rel = os.path.relpath(abs_path, master)
                        except ValueError:
                            rel = abs_path
                    else:
                        rel = abs_path
                    rel = rel.replace('\\', '/')
                    disk_files[rel] = abs_path

        # Fichiers connus en DB
        db_files: dict[str, sqlite3.Row] = {}
        for row in self._conn.execute(
            "SELECT id, relative_path, file_modified_at FROM comics"
        ).fetchall():
            db_files[row['relative_path']] = row

        stats = {'new': 0, 'updated': 0, 'deleted': 0,
                 'new_paths': [], 'updated_paths': [], 'deleted_ids': []}
        now = _now_iso()
        total = len(disk_files) + len(db_files)
        done = 0

        # Nouveaux et modifiés
        for rel, abs_path in disk_files.items():
            if stop_event and stop_event.is_set():
                break
            mtime = _file_mtime_iso(abs_path)
            fname = os.path.basename(abs_path)
            done += 1
            pct = int(done * 100 / total) if total > 0 else 100

            if rel not in db_files:
                if progress_callback:
                    progress_callback(('new', fname, pct))
                self._index_file(rel, abs_path, mtime, now)
                stats['new'] += 1
                stats['new_paths'].append(abs_path)
            else:
                db_row = db_files[rel]
                db_mtime = db_row['file_modified_at']
                if mtime and db_mtime and mtime > db_mtime:
                    if progress_callback:
                        progress_callback(('updated', fname, pct))
                    self._index_file(rel, abs_path, mtime, now,
                                     existing_id=db_row['id'])
                    stats['updated'] += 1
                    stats['updated_paths'].append(abs_path)

        # Suppressions
        for rel, row in db_files.items():
            if stop_event and stop_event.is_set():
                break
            if rel not in disk_files:
                fname = os.path.basename(rel)
                done += 1
                pct = int(done * 100 / total) if total > 0 else 100
                if progress_callback:
                    progress_callback(('deleted', fname, pct))
                stats['deleted_ids'].append(row['id'])
                self._conn.execute("DELETE FROM comics WHERE id=?", (row['id'],))
                stats['deleted'] += 1

        self._conn.commit()
        return stats

    def _index_file(self, rel_path, abs_path, mtime, now, existing_id=None):
        """Insère ou met à jour un enregistrement comics."""
        fname = os.path.basename(abs_path)
        ext = _ext(abs_path)
        try:
            size = os.path.getsize(abs_path)
        except Exception:
            size = None

        can = ext in _ARCHIVE_EXTS
        has = False
        meta = {}
        page_count = None

        if can:
            has = _has_comicinfo_in_archive(abs_path)
            if has:
                xml_bytes = _read_comicinfo_from_archive(abs_path)
                if xml_bytes:
                    from modules.qt.comic_info import parse_comic_info_xml
                    parsed = parse_comic_info_xml(xml_bytes)
                    if parsed:
                        meta = parsed

        # Toujours compter les vraies pages dans l'archive (plus fiable que PageCount XML)
        page_count = _count_pages(abs_path)

        # Préserver is_read si mise à jour
        is_read = 0
        if existing_id is not None:
            row = self._conn.execute(
                "SELECT is_read FROM comics WHERE id=?", (existing_id,)
            ).fetchone()
            if row:
                is_read = row['is_read']

        fields = {
            'relative_path':    rel_path,
            'filename':         fname,
            'file_extension':   ext,
            'file_size':        size,
            'file_modified_at': mtime,
            'indexed_at':       now,
            'has_comicinfo':    int(has),
            'can_have_comicinfo': int(can),
            'is_read':          is_read,
            'page_count':       page_count,
        }
        for f in _COMICINFO_FIELDS:
            v = meta.get(f, '')
            fields[f] = v if v else None
        fields['page_count'] = page_count

        cols = ', '.join(fields.keys())
        placeholders = ', '.join(['?'] * len(fields))
        vals = list(fields.values())

        if existing_id is not None:
            sets = ', '.join(f"{k}=?" for k in fields if k != 'relative_path')
            set_vals = [fields[k] for k in fields if k != 'relative_path']
            self._conn.execute(
                f"UPDATE comics SET {sets} WHERE id=?",
                set_vals + [existing_id]
            )
        else:
            self._conn.execute(
                f"INSERT OR REPLACE INTO comics ({cols}) VALUES ({placeholders})",
                vals
            )

    def reindex_files(self, abs_paths: list[str]):
        """Réindexe uniquement les fichiers donnés (après modification des métadonnées)."""
        master = self.get_master_dir()
        now = _now_iso()
        for abs_path in abs_paths:
            if not os.path.isfile(abs_path):
                continue
            if master:
                try:
                    rel = os.path.relpath(abs_path, master)
                except ValueError:
                    rel = abs_path
            else:
                rel = abs_path
            rel = rel.replace('\\', '/')
            mtime = _file_mtime_iso(abs_path)
            row = self._conn.execute(
                "SELECT id FROM comics WHERE relative_path=?", (rel,)
            ).fetchone()
            existing_id = row['id'] if row else None
            self._index_file(rel, abs_path, mtime, now, existing_id=existing_id)
        self._conn.commit()

    # ── is_read ───────────────────────────────────────────────────────────────

    def set_read(self, ids: list[int], is_read: bool):
        """Marque les comics (par id) comme lus ou non lus."""
        _backup(self._db_path)
        val = 1 if is_read else 0
        self._conn.executemany(
            "UPDATE comics SET is_read=? WHERE id=?",
            [(val, i) for i in ids]
        )
        self._conn.commit()

    # ── Recherche ─────────────────────────────────────────────────────────────

    # Colonnes autorisées pour la recherche (whitelist anti-injection)
    _SEARCHABLE = {
        'is_read', 'series', 'volume', 'number', 'writer', 'penciller',
        'inker', 'publisher', 'characters', 'teams', 'year', 'month', 'day',
        'page_count', 'file_size', 'filename', 'file_extension', 'title',
        'colorist', 'letterer', 'cover_artist', 'editor', 'imprint', 'genre',
        'language_iso', 'age_rating', 'black_and_white', 'manga', 'locations',
        'story_arc', 'summary', 'web', 'has_comicinfo', 'can_have_comicinfo',
        'relative_path', 'file_modified_at', 'indexed_at',
    }

    # Champs stockés TEXT mais comparés numériquement → CAST(col AS INTEGER)
    _INT_CAST_FIELDS = {'number', 'volume', 'year', 'month', 'day', 'page_count', 'file_size'}

    _OP_MAP = {
        'contains':     ("LIKE ?",            lambda v: f"%{v}%"),
        'not_contains': ("NOT LIKE ?",        lambda v: f"%{v}%"),
        'is':           ("= ?",               lambda v: v),
        'empty':        ("IS NULL OR {col} = ''", None),
        'not_empty':    ("IS NOT NULL AND {col} != ''", None),
        'eq':           ("= ?",               lambda v: v),
        'neq':          ("!= ?",              lambda v: v),
        'gt':           ("> ?",               lambda v: v),
        'lt':           ("< ?",               lambda v: v),
        'gte':          (">= ?",              lambda v: v),
        'lte':          ("<= ?",              lambda v: v),
        'between':      ("BETWEEN ? AND ?",   None),
        'true':         ("= 1",               None),
        'false':        ("= 0",               None),
        'before':       ("< ?",               lambda v: _date_bound_before(v)),
        'after':        (">= ?",              lambda v: _date_bound_after(v)),
    }

    def search(self, criteria: list[dict], order_by: str = 'series',
               order_asc: bool = True,
               progress_callback=None) -> list[sqlite3.Row]:
        """
        criteria : liste de dict {field, op, value, link}
            field  : nom de colonne (whitelist)
            op     : clé dans _OP_MAP
            value  : valeur(s) — pour 'between', tuple (v1, v2)
            link   : 'and' | 'or' (ignoré pour le 1er critère)

        Retourne tous les enregistrements si criteria est vide.
        progress_callback(percent: int) : appelé pendant fetchmany si fourni.
        """
        # Regrouper les critères par champ (ordre d'apparition conservé)
        # Chaque groupe = liste de (clause_sql, params_list, link_within_group)
        # Les groupes sont reliés entre eux par AND.
        # À l'intérieur d'un groupe, les clauses sont reliées par leur link (ET/OU).
        from collections import OrderedDict
        groups: OrderedDict[str, list] = OrderedDict()
        group_params: dict[str, list] = {}

        for crit in criteria:
            field = crit.get('field', '')
            op    = crit.get('op', 'contains')
            value = crit.get('value', '')
            link  = crit.get('link', 'and').upper()

            if field not in self._SEARCHABLE:
                continue
            if op not in self._OP_MAP:
                continue

            op_sql, transform = self._OP_MAP[op]
            p: list = []

            # Champs numériques stockés en TEXT : comparer via CAST pour éviter l'ordre lexicographique
            col_expr = f"CAST({field} AS INTEGER)" if field in self._INT_CAST_FIELDS else field

            if '{col}' in op_sql:
                clause = f"{field} {op_sql.format(col=field)}"
            elif transform is None and op == 'between':
                v1, v2 = (value if isinstance(value, (list, tuple)) and len(value) == 2
                          else (value, value))
                try:
                    v1, v2 = int(v1), int(v2)
                except (ValueError, TypeError):
                    pass
                clause = f"{col_expr} BETWEEN ? AND ?"
                p = [v1, v2]
            elif transform is None:
                clause = f"{col_expr} {op_sql}"
            else:
                clause = f"{col_expr} {op_sql}"
                if field in self._INT_CAST_FIELDS:
                    try:
                        p = [int(transform(value))]
                    except (ValueError, TypeError):
                        p = [transform(value)]
                else:
                    p = [transform(value)]

            if field not in groups:
                groups[field] = []
                group_params[field] = []
            groups[field].append((clause, p, link))
            group_params[field].extend(p)

        # Construire le WHERE : chaque groupe entre parenthèses, groupes reliés par AND
        params = []
        group_sqls = []
        for field, entries in groups.items():
            parts = []
            for j, (clause, p, link) in enumerate(entries):
                params.extend(p)
                if j == 0:
                    parts.append(clause)
                else:
                    parts.append(f"{link} {clause}")
            group_sqls.append(f"({' '.join(parts)})")

        where_sql = ' AND '.join(group_sqls)
        if where_sql:
            where_sql = 'WHERE ' + where_sql

        order_col = order_by if order_by in self._SEARCHABLE else 'series'
        direction = 'ASC' if order_asc else 'DESC'

        # NULL en dernier quelle que soit la direction
        null_order = 'NULLS LAST' if order_asc else 'NULLS FIRST'
        sql = f"SELECT * FROM comics {where_sql} ORDER BY {order_col} {direction} {null_order}"

        if progress_callback is None:
            return self._conn.execute(sql, params).fetchall()

        total = self._conn.execute(
            f"SELECT COUNT(*) FROM comics {where_sql}", params
        ).fetchone()[0]

        cursor = self._conn.execute(sql, params)
        rows = []
        chunk = 500
        while True:
            batch = cursor.fetchmany(chunk)
            if not batch:
                break
            rows.extend(batch)
            if total > 0:
                progress_callback(int(len(rows) * 100 / total))
        return rows

    def search_cursor(self, criteria: list[dict], order_by: str = 'series',
                      order_asc: bool = True):
        """Comme search() mais retourne (total, cursor) sans tout charger en mémoire."""
        # Réutilise la même logique de construction SQL que search()
        from collections import OrderedDict
        groups: OrderedDict[str, list] = OrderedDict()
        group_params: dict[str, list] = {}
        for crit in criteria:
            field = crit.get('field', '')
            op    = crit.get('op', 'contains')
            value = crit.get('value', '')
            link  = crit.get('link', 'and').upper()
            if field not in self._SEARCHABLE or op not in self._OP_MAP:
                continue
            op_sql, transform = self._OP_MAP[op]
            p: list = []
            col_expr = f"CAST({field} AS INTEGER)" if field in self._INT_CAST_FIELDS else field
            if '{col}' in op_sql:
                clause = f"{field} {op_sql.format(col=field)}"
            elif transform is None and op == 'between':
                v1, v2 = (value if isinstance(value, (list, tuple)) and len(value) == 2 else (value, value))
                try: v1, v2 = int(v1), int(v2)
                except (ValueError, TypeError): pass
                clause = f"{col_expr} BETWEEN ? AND ?"
                p = [v1, v2]
            elif transform is None:
                clause = f"{col_expr} {op_sql}"
                p = [value]
            else:
                clause = f"{field} {op_sql}"
                p = [transform(value)]
            if field not in groups:
                groups[field] = []
                group_params[field] = []
            groups[field].append((clause, p, link))
            group_params[field].extend(p)
        params = []
        group_sqls = []
        for field, entries in groups.items():
            parts = []
            for j, (clause, p, link) in enumerate(entries):
                params.extend(p)
                parts.append(clause if j == 0 else f"{link} {clause}")
            group_sqls.append(f"({' '.join(parts)})")
        where_sql = ('WHERE ' + ' AND '.join(group_sqls)) if group_sqls else ''
        order_col = order_by if order_by in self._SEARCHABLE else 'series'
        direction = 'ASC' if order_asc else 'DESC'
        null_order = 'NULLS LAST' if order_asc else 'NULLS FIRST'
        sql = f"SELECT * FROM comics {where_sql} ORDER BY {order_col} {direction} {null_order}"
        total = self._conn.execute(
            f"SELECT COUNT(*) FROM comics {where_sql}", params
        ).fetchone()[0]
        cursor = self._conn.execute(sql, params)
        return total, cursor

    # ── Configuration colonnes ────────────────────────────────────────────────

    def get_columns_config(self) -> list[str] | None:
        """Retourne la liste ordonnée des colonnes visibles, ou None si absent."""
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key='columns_config'"
        ).fetchone()
        if row:
            import json
            try:
                return json.loads(row['value'])
            except Exception:
                return None
        return None

    def set_columns_config(self, fields: list[str]):
        """Sauvegarde la liste ordonnée des colonnes visibles."""
        import json
        self._conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('columns_config', ?)",
            (json.dumps(fields),)
        )
        self._conn.commit()

    # ── Accès direct ──────────────────────────────────────────────────────────

    def get_by_id(self, comic_id: int) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM comics WHERE id=?", (comic_id,)
        ).fetchone()

    def get_by_filepath(self, abs_path: str) -> 'sqlite3.Row | None':
        """Retourne la row d'un comic par son chemin absolu."""
        master = self.get_master_dir()
        try:
            rel = os.path.relpath(abs_path, master) if master else abs_path
        except ValueError:
            rel = abs_path
        rel = rel.replace('\\', '/')
        return self._conn.execute(
            "SELECT * FROM comics WHERE relative_path=?", (rel,)
        ).fetchone()

    def get_absolute_path(self, comic_id: int) -> str | None:
        """Reconstitue le chemin absolu depuis relative_path + master_dir."""
        row = self.get_by_id(comic_id)
        if not row:
            return None
        master = self.get_master_dir()
        rel = row['relative_path']
        if master:
            safe = safe_join(master, rel)
            if safe is not None:
                return safe
            return os.path.normpath(os.path.join(master, rel))
        return rel

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM comics").fetchone()
        return row[0] if row else 0
