"""Requêtes à l'API ComicVine (comicvine_scraper._get_json) et erreurs
montrées à l'utilisateur.

Vérifié, sans réseau (module `requests` remplacé, attentes supprimées) :
- réponse correcte rendue, clé API et format passés en paramètres ;
- clé refusée (401/403) et codes d'erreur ComicVine connus : erreur
  traduisible, sans nouvelle tentative ;
- code inconnu : message gardé, sans rien qui ressemble à une balise HTML
  (un QLabel le prendrait pour du HTML) ;
- délai dépassé ou connexion impossible : trois tentatives espacées, puis
  erreur traduisible ; une tentative réussie après un échec suffit ;
- toute autre erreur : message sans la clé API en clair (l'URL complète,
  clé comprise, figure dans le texte des exceptions de `requests`) — règle
  de sécurité n° 2 de CLAUDE.md ;
- réponse d'une forme inattendue (page HTML, JSON qui n'est pas un objet) :
  erreur traduisible, sans nouvelle tentative ;
- lecture des réponses (search_series, get_series_issues, get_issue_details,
  get_series_details, get_series_summary) : résultats vides, champs absents ou
  nuls, résultat unique donné comme objet au lieu d'une liste, éléments qui ne
  sont pas des objets ignorés, nombre total absent ou qui n'est pas un nombre
  (remplacé par celui des résultats reçus), adresses d'image autres que
  http/https refusées ;
- transport d'une erreur vers l'interface par un signal Qt (texte ou clé de
  traduction, retraduite à l'affichage).
"""
import types

import pytest

import modules.qt.comicvine_scraper as scraper
from modules.qt.comicvine_scraper import (ComicVineNetworkError, _get_json, _redact_api_key,
                                          error_message_fn, error_to_signal_payload)

SECRET = "SECRET123"
URL = "https://comicvine.gamespot.com/api/issue/4000-1/"


class _Timeout(Exception):
    pass


class _ConnectionError(Exception):
    pass


class _HTTPError(Exception):
    pass


class Response:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self._data = data if data is not None else {"status_code": 1, "results": {"id": 1}}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise _HTTPError(f"{self.status_code} Server Error for url: {URL}?api_key={SECRET}"
                             f"&format=json")

    def json(self):
        return self._data


class FakeRequests:
    """Rend, à chaque appel, la réponse suivante (ou lève l'exception suivante)."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.exceptions = types.SimpleNamespace(Timeout=_Timeout, ConnectionError=_ConnectionError)

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def network(monkeypatch):
    """Installe un faux `requests` ; rend (fabrique, attentes faites)."""
    sleeps = []
    monkeypatch.setattr(scraper, "_REQUESTS_AVAILABLE", True)
    monkeypatch.setattr(scraper, "_wait_rate_limit", lambda: None)
    monkeypatch.setattr(scraper, "time", types.SimpleNamespace(sleep=sleeps.append))

    def install(*outcomes):
        fake = FakeRequests(*outcomes)
        monkeypatch.setattr(scraper, "_requests", fake)
        return fake
    return install, sleeps


def test_correct_answer_is_returned_with_key_and_format(network):
    install, sleeps = network
    fake = install(Response())
    data = _get_json(URL, SECRET, {"field_list": "id"})
    assert data["results"] == {"id": 1}
    params = fake.calls[0]["params"]
    assert params["api_key"] == SECRET and params["format"] == "json"
    assert params["field_list"] == "id"
    assert fake.calls[0]["timeout"] and fake.calls[0]["headers"]["User-Agent"]
    assert sleeps == []


@pytest.mark.parametrize("status", [401, 403])
def test_refused_key_is_reported_without_retry(network, status):
    install, _sleeps = network
    fake = install(Response(status))
    with pytest.raises(ComicVineNetworkError) as raised:
        _get_json(URL, SECRET)
    assert raised.value.translation_key == "comicvine.error_api_invalid_key"
    assert len(fake.calls) == 1


@pytest.mark.parametrize("code, key", [(100, "comicvine.error_api_invalid_key"),
                                       (101, "comicvine.error_api_not_found"),
                                       (107, "comicvine.error_api_rate_limit")])
def test_known_comicvine_error_codes_are_translatable(network, code, key):
    install, _sleeps = network
    fake = install(Response(data={"status_code": code, "error": "x"}))
    with pytest.raises(ComicVineNetworkError) as raised:
        _get_json(URL, SECRET)
    assert raised.value.translation_key == key
    assert len(fake.calls) == 1


def test_unknown_error_code_keeps_its_message_without_html(network):
    install, _sleeps = network
    install(Response(data={"status_code": 999, "error": "<img src=x>panne"}))
    with pytest.raises(RuntimeError) as raised:
        _get_json(URL, SECRET)
    message = str(raised.value)
    assert "999" in message and "panne" in message
    assert "<" not in message and ">" not in message


def test_timeouts_are_retried_then_reported(network):
    install, sleeps = network
    fake = install(_Timeout("read timeout"), _Timeout("read timeout"), _Timeout("read timeout"))
    with pytest.raises(ComicVineNetworkError) as raised:
        _get_json(URL, SECRET)
    assert raised.value.translation_key == "comicvine.error_network_timeout"
    assert len(fake.calls) == 3
    assert sleeps == scraper._RETRY_DELAYS              # attente entre deux tentatives


def test_connection_failure_is_reported_as_such(network):
    install, _sleeps = network
    install(*[_ConnectionError("Name resolution failed")] * 3)
    with pytest.raises(ComicVineNetworkError) as raised:
        _get_json(URL, SECRET)
    assert raised.value.translation_key == "comicvine.error_network_connection"


def test_one_failure_then_success_is_enough(network):
    install, sleeps = network
    fake = install(_Timeout("read timeout"), Response())
    assert _get_json(URL, SECRET)["results"] == {"id": 1}
    assert len(fake.calls) == 2 and sleeps == [scraper._RETRY_DELAYS[0]]


def test_other_errors_never_show_the_api_key(network):
    install, _sleeps = network
    install(*[Response(500)] * 3)
    with pytest.raises(RuntimeError) as raised:
        _get_json(URL, SECRET)
    message = str(raised.value)
    assert SECRET not in message
    assert "api_key=***" in message


def test_redaction_of_the_api_key():
    assert _redact_api_key(f"GET {URL}?api_key={SECRET}&format=json") == \
        f"GET {URL}?api_key=***&format=json"
    assert _redact_api_key(f"a?api_key={SECRET} b?api_key=AUTRE") == "a?api_key=*** b?api_key=***"
    assert _redact_api_key("rien à masquer") == "rien à masquer"


class NotJson(Response):
    """Page HTML (maintenance, portail captif) au lieu de JSON."""

    def json(self):
        raise ValueError("Expecting value: line 1 column 1 (char 0)")


@pytest.mark.parametrize("answer", [NotJson(), Response(data=["inattendu"])],
                         ids=["not_json", "not_an_object"])
def test_answer_of_unexpected_form_is_reported_clearly_without_retry(network, answer):
    install, sleeps = network
    fake = install(answer)
    with pytest.raises(ComicVineNetworkError) as raised:
        _get_json(URL, SECRET)
    assert raised.value.translation_key == "comicvine.error_api_bad_answer"
    assert len(fake.calls) == 1 and sleeps == []


@pytest.fixture
def answers(monkeypatch):
    """Remplace _get_json : rend, par début d'adresse, la réponse donnée."""
    table = {}

    def fake_get_json(url, api_key, params=None):
        return next(data for prefix, data in table.items() if url.startswith(prefix))
    monkeypatch.setattr(scraper, "_get_json", fake_get_json)
    return table


def test_empty_results_give_nothing_without_error(answers):
    answers[scraper._API_BASE] = {"status_code": 1, "results": []}
    assert scraper.search_series(SECRET, "Aaaa") == ([], 0)
    assert scraper.get_series_issues(SECRET, 1) == ([], 0)
    answers[scraper._API_BASE] = {"status_code": 1, "results": None}
    assert scraper.get_issue_details(SECRET, 1) is None
    assert scraper.get_series_details(SECRET, 1) is None
    assert scraper.get_series_summary(SECRET, 1) is None


def test_series_with_missing_or_null_fields(answers):
    answers[scraper._API_BASE] = {"status_code": 1, "results": [
        {"id": 7},
        {"id": 8, "name": "Aaaa", "start_year": None, "publisher": "Bbbb", "image": None}]}
    series, total = scraper.search_series(SECRET, "Aaaa")
    assert total == 2                                 # total absent : résultats reçus
    assert series[0] == {"id": 7, "name": "", "start_year": "", "publisher": "",
                         "issue_count": "", "image_url": None}
    assert series[1]["name"] == "Aaaa" and series[1]["publisher"] == ""   # éditeur non objet


def test_single_result_given_as_an_object_is_a_list(answers):
    answers[scraper._API_BASE] = {"status_code": 1, "number_of_total_results": 1,
                                  "results": {"id": 3, "issue_number": " 1 ", "name": None}}
    assert scraper.get_series_issues(SECRET, 9) == (
        [{"id": 3, "issue_number": "1", "name": "", "image_url": None}], 1)


def test_results_that_are_not_objects_are_ignored(answers):
    answers[scraper._API_BASE] = {"status_code": 1, "number_of_total_results": 3,
                                  "results": [None, "Aaaa", {"id": 4}]}
    series, total = scraper.search_series(SECRET, "Aaaa")
    assert [s["id"] for s in series] == [4] and total == 3
    answers[scraper._API_BASE] = {"status_code": 1, "results": "Aaaa"}
    assert scraper.get_series_issues(SECRET, 1) == ([], 0)
    answers[scraper._API_BASE] = {"status_code": 1, "results": [{"id": 1}]}
    assert scraper.get_issue_details(SECRET, 1) is None
    assert scraper.get_series_details(SECRET, 1) is None
    assert scraper.get_series_summary(SECRET, 1) is None


@pytest.mark.parametrize("announced", [None, "", "beaucoup", [3]])
def test_total_that_is_not_a_number_counts_the_results_received(answers, announced):
    answers[scraper._API_BASE] = {"status_code": 1, "number_of_total_results": announced,
                                  "results": [{"id": 1}, {"id": 2}]}
    assert scraper.search_series(SECRET, "Aaaa")[1] == 2
    assert scraper.search_series(SECRET, "Aaaa", page=3)[1] == 202   # 100 par page avant
    assert scraper.get_series_issues(SECRET, 1)[1] == 2


def test_issue_with_null_fields_gives_empty_metadata(answers):
    answers[scraper._API_BASE] = {"status_code": 1, "results": {
        "name": None, "volume": None, "issue_number": None, "cover_date": None,
        "description": None, "site_detail_url": None, "person_credits": None,
        "character_credits": None, "team_credits": None, "location_credits": None,
        "story_arc_credits": None, "image": None}}
    meta = scraper.get_issue_details(SECRET, 1)
    assert meta["title"] == meta["series"] == meta["number"] == meta["summary"] == ""
    assert meta["web"] == meta["characters"] == meta["story_arc"] == ""
    assert "year" not in meta and "writer" not in meta and meta["_cover_image_url"] is None


def test_issue_credits_given_as_a_single_object(answers):
    answers[f"{scraper._API_BASE}/volume/"] = {"status_code": 1, "results": {
        "start_year": "2001", "publisher": {"name": "Cccc"}, "genres": {"name": "Dddd"}}}
    answers[scraper._API_BASE] = {"status_code": 1, "results": {
        "name": "Eeee", "volume": {"id": 5, "name": "Aaaa"}, "cover_date": "2001-03-09",
        "person_credits": {"name": "Ffff", "role": "writer, artist"},
        "character_credits": {"name": "Gggg"}}}
    meta = scraper.get_issue_details(SECRET, 1)
    assert (meta["series"], meta["volume"], meta["publisher"], meta["genre"]) == \
        ("Aaaa", "2001", "Cccc", "Dddd")
    assert (meta["year"], meta["month"], meta["day"]) == ("2001", "3", "9")
    assert meta["writer"] == meta["penciller"] == "Ffff" and meta["characters"] == "Gggg"


def test_only_web_image_addresses_are_kept():
    assert scraper._parse_image_url({"small_url": "file://srv/x.jpg",
                                     "medium_url": "https://site/x.jpg"}) == "https://site/x.jpg"
    assert scraper._parse_image_url({"small_url": "\\\\serveur\\x.jpg"}) is None
    assert scraper._parse_image_url({"small_url": 12}) is None
    assert scraper._parse_image_url("https://site/x.jpg") is None    # pas un objet


def test_network_error_crosses_a_signal_as_a_translation_key():
    payload = error_to_signal_payload(ComicVineNetworkError("comicvine.error_network_timeout"))
    assert "comicvine.error_network_timeout" in payload
    assert error_message_fn(payload)() != payload       # retraduit à l'affichage
    plain = error_to_signal_payload(RuntimeError("message technique"))
    assert plain == "message technique"
    assert error_message_fn(plain)() == "message technique"
