"""Nettoyage des descriptions ComicVine (comicvine_scraper._strip_html).

Une description ComicVine (wiki éditable par tous) peut écrire une balise en
entités (&lt;img …&gt;) : décodée, elle redeviendrait une vraie balise dans le
Summary de ComicInfo.xml.
"""
from modules.qt.comicvine_scraper import _strip_html


def test_real_tags_are_removed():
    assert _strip_html("<p>Hello <b>world</b></p>") == "Hello world"


def test_tags_written_as_entities_do_not_come_back():
    out = _strip_html('Intro &lt;img src="//serveur/partage/x.png"&gt; fin')
    assert "<" not in out and ">" not in out
    assert out == "Intro fin"


def test_closing_tag_written_as_entities_is_removed():
    assert _strip_html("a &lt;/script&gt; b") == "a b"


def test_comparison_signs_are_kept():
    assert _strip_html("x &lt; y and z &gt; w") == "x < y and z > w"


def test_other_entities_are_decoded():
    assert _strip_html("Tom &amp; Jerry &quot;cats&quot;") == 'Tom & Jerry "cats"'
