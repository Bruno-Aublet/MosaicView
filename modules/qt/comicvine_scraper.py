# -------------------------
# Récupération de métadonnées depuis l'API ComicVine
# Inspiré de comic-vine-scraper par Cory Banack (Apache 2.0)
# https://github.com/cbanack/comic-vine-scraper
# -------------------------
import re
import time
import urllib.parse

try:
    import requests as _requests
    _REQUESTS_AVAILABLE = True
except ImportError:
    _REQUESTS_AVAILABLE = False

_API_BASE = "https://comicvine.gamespot.com/api"
_CLIENT_TAG = "&client=mosaicview"

# Rate limiting : 1 requête/seconde minimum (ComicVine l'exige)
_next_query_time = 0.0
_QUERY_DELAY_S = 2.0


# ---------------------------------------------------------------------------
# Helpers internes
# ---------------------------------------------------------------------------

def _wait_rate_limit():
    global _next_query_time
    now = time.monotonic()
    wait = _next_query_time - now
    if wait > 0:
        time.sleep(wait)
    _next_query_time = time.monotonic() + _QUERY_DELAY_S


_MAX_RETRIES = 3
_RETRY_DELAYS = [5, 15, 30]   # secondes entre tentatives


def _get_json(url, api_key, params=None):
    """
    Effectue une requête GET sur l'API ComicVine et retourne le JSON parsé.
    Retry automatique (jusqu'à 3 tentatives) en cas de timeout ou d'erreur réseau.
    """
    if not _REQUESTS_AVAILABLE:
        raise RuntimeError("Le module 'requests' n'est pas installé.")

    full_params = {"api_key": api_key, "format": "json"}
    if params:
        full_params.update(params)

    headers = {"User-Agent": "MosaicView/1.0 (comic archive editor)"}
    last_exc = None

    for attempt in range(_MAX_RETRIES):
        _wait_rate_limit()
        try:
            response = _requests.get(url, params=full_params, headers=headers, timeout=30)
            response.raise_for_status()
            data = response.json()
            status = data.get("status_code", 0)
            if status != 1:
                error = data.get("error", "unknown error")
                raise RuntimeError(f"ComicVine API error {status}: {error}")
            return data
        except RuntimeError:
            raise
        except Exception as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_RETRY_DELAYS[attempt])

    raise last_exc


def _strip_html(text):
    """Supprime les balises HTML et nettoie le texte (comme cvdb.__issue_parse_summary)."""
    if not text:
        return ""
    text = re.sub(r'<[bB][rR] ?/?>|<[Pp] ?>', '\n', text)
    text = re.sub(r'<.*?>', '', text)
    text = re.sub(r'&nbsp;?', ' ', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&quot;', '"', text)
    text = re.sub(r'&lt;', '<', text)
    text = re.sub(r'&gt;', '>', text)
    text = re.sub(r' {2,}', ' ', text)
    text = re.sub(r'(?is)list of covers.*$', '', text)
    return text.strip()


def _join_names(items, key="name"):
    """Convertit une liste de dicts ComicVine en chaîne séparée par des virgules."""
    if not items:
        return ""
    if isinstance(items, dict):
        items = [items]
    return ", ".join(i.get(key, "") for i in items if i.get(key))


def _parse_image_url(image_dict):
    """Extrait la meilleure URL d'image disponible dans un dict ComicVine 'image'."""
    if not isinstance(image_dict, dict):
        return None
    for key in ("small_url", "medium_url", "large_url", "super_url", "thumb_url"):
        url = image_dict.get(key)
        if url and isinstance(url, str):
            return url
    return None


# ---------------------------------------------------------------------------
# API publique
# ---------------------------------------------------------------------------

def search_series(api_key, search_terms, page=1):
    """
    Recherche des séries (volumes) sur ComicVine correspondant à search_terms.

    Retourne une liste de dicts :
      { 'id', 'name', 'start_year', 'publisher', 'issue_count', 'image_url' }

    Peut lever une exception en cas de problème réseau ou API.
    """
    url = f"{_API_BASE}/search/"
    params = {
        "query": search_terms,
        "resources": "volume",
        "field_list": "id,name,start_year,publisher,count_of_issues,image",
        "limit": 100,
    }
    if page > 1:
        params["page"] = page
        params["offset"] = (page - 1) * 100

    data = _get_json(url, api_key, params)
    results = data.get("results") or []
    if isinstance(results, dict):
        results = [results]

    series_list = []
    for vol in results:
        pub = vol.get("publisher") or {}
        series_list.append({
            "id": vol.get("id"),
            "name": vol.get("name", ""),
            "start_year": (vol.get("start_year") or "").rstrip("- "),
            "publisher": pub.get("name", "") if isinstance(pub, dict) else "",
            "issue_count": vol.get("count_of_issues", ""),
            "image_url": _parse_image_url(vol.get("image")),
        })

    return series_list, int(data.get("number_of_total_results", 0))


def get_series_issues(api_key, series_id, page=1):
    """
    Retourne la liste des issues d'une série ComicVine.

    Retourne une liste de dicts :
      { 'id', 'issue_number', 'name', 'image_url' }

    Peut lever une exception en cas de problème réseau ou API.
    """
    url = f"{_API_BASE}/issues/"
    params = {
        "filter": f"volume:{series_id}",
        "field_list": "id,issue_number,name,image",
        "limit": 100,
    }
    if page > 1:
        params["page"] = page
        params["offset"] = (page - 1) * 100

    data = _get_json(url, api_key, params)
    results = data.get("results") or []
    if isinstance(results, dict):
        results = [results]

    issues = []
    for iss in results:
        issues.append({
            "id": iss.get("id"),
            "issue_number": (iss.get("issue_number") or "").strip(),
            "name": (iss.get("name") or "").strip(),
            "image_url": _parse_image_url(iss.get("image")),
        })

    return issues, int(data.get("number_of_total_results", 0))


def get_issue_details(api_key, issue_id):
    """
    Récupère les détails complets d'un issue ComicVine et les retourne
    sous forme d'un dict directement mappé sur les champs de comic_metadata.

    Retourne un dict avec les clés de comic_metadata (titre, series, auteurs...)
    ou None en cas d'erreur.
    """
    url = f"{_API_BASE}/issue/4000-{issue_id}/"
    data = _get_json(url, api_key)
    r = data.get("results")
    if not r:
        return None

    meta = {}

    # Titre de l'issue
    meta["title"] = (r.get("name") or "").strip()

    # Série
    volume = r.get("volume") or {}
    meta["series"] = (volume.get("name") or "").strip()

    # Numéro
    meta["number"] = (r.get("issue_number") or "").strip()

    # Date de couverture (cover_date = "YYYY-MM-DD")
    cover_date = r.get("cover_date") or ""
    if cover_date:
        parts = cover_date.split("-")
        meta["year"] = parts[0] if len(parts) >= 1 else ""
        meta["month"] = parts[1].lstrip("0") if len(parts) >= 2 else ""
        meta["day"] = parts[2].lstrip("0") if len(parts) >= 3 else ""

    # Résumé (nettoyer le HTML)
    meta["summary"] = _strip_html(r.get("description") or "")

    # URL de la page ComicVine
    meta["web"] = r.get("site_detail_url") or ""

    # Crédits créatifs
    role_map = {
        "writer":    "writer",
        "penciler":  "penciller",
        "artist":    "penciller",
        "inker":     "inker",
        "cover":     "cover_artist",
        "editor":    "editor",
        "colorer":   "colorist",
        "colorist":  "colorist",
        "letterer":  "letterer",
    }
    role_buckets = {v: [] for v in set(role_map.values())}

    person_credits = r.get("person_credits") or []
    if isinstance(person_credits, dict):
        person_credits = [person_credits]
    for person in person_credits:
        name = (person.get("name") or "").strip()
        roles_str = person.get("role") or ""
        for role in [ro.strip() for ro in roles_str.split(",")]:
            target = role_map.get(role)
            if target and name:
                role_buckets[target].append(name)

    for field, names in role_buckets.items():
        if names:
            meta[field] = ", ".join(names)

    # Personnages, équipes, lieux, story arcs
    chars = r.get("character_credits") or []
    if isinstance(chars, dict):
        chars = [chars]
    meta["characters"] = _join_names(chars)

    teams = r.get("team_credits") or []
    if isinstance(teams, dict):
        teams = [teams]
    meta["teams"] = _join_names(teams)

    locations = r.get("location_credits") or []
    if isinstance(locations, dict):
        locations = [locations]
    meta["locations"] = _join_names(locations)

    arcs = r.get("story_arc_credits") or []
    if isinstance(arcs, dict):
        arcs = [arcs]
    meta["story_arc"] = _join_names(arcs)

    # URL image de couverture (pour affichage éventuel dans l'UI)
    meta["_cover_image_url"] = _parse_image_url(r.get("image"))

    return meta


def get_series_details(api_key, series_id):
    """
    Récupère le nom de l'éditeur et l'année de début d'une série.
    Retourne { 'publisher', 'start_year' } ou None.
    """
    url = f"{_API_BASE}/volume/4050-{series_id}/"
    params = {"field_list": "name,start_year,publisher,image,count_of_issues,id"}
    data = _get_json(url, api_key, params)
    r = data.get("results")
    if not r:
        return None
    pub = r.get("publisher") or {}
    return {
        "publisher": pub.get("name", "") if isinstance(pub, dict) else "",
        "start_year": (r.get("start_year") or "").rstrip("- "),
    }
