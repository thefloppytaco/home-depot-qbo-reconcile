#!/usr/bin/env python3
"""
Unit tests for the string/regex helpers in src/parse_receipts.py.

Requirements: `pip install pytest` (the only dependency; the pipeline scripts
themselves stay stdlib-only). No `pdftotext`/Poppler needed here either --
these tests exercise pure functions directly, no PDF or receipt files involved.

src/ has no __init__.py (it's a scripts folder, not a package), so the module is
loaded directly from its file path via importlib rather than a normal import.

Run with: pytest -q
"""
import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO_ROOT / "src" / "parse_receipts.py"

_spec = importlib.util.spec_from_file_location("parse_receipts", MODULE_PATH)
parse_receipts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(parse_receipts)


# --- norm_locator --------------------------------------------------------

def test_norm_locator_strips_leading_zeros_on_register_and_txn():
    # header style ("00019") vs. ORIG REC style ("090") must normalize the same way
    assert parse_receipts.norm_locator("2584", "00019", "012498", "06/15/26") == \
        "2584|19|12498|06/15/26"
    assert parse_receipts.norm_locator("2584", "090", "12498", "06/15/26") == \
        "2584|90|12498|06/15/26"


def test_norm_locator_header_and_orig_rec_forms_match():
    header_locator = parse_receipts.norm_locator("0000", "00052", "71234", "06/20/26")
    orig_rec_locator = parse_receipts.norm_locator("0000", "052", "71234", "06/20/26")
    assert header_locator == orig_rec_locator == "0000|52|71234|06/20/26"


def test_norm_locator_all_zeros_falls_back_to_literal_zero():
    # "0000".lstrip("0") == "" -- must fall back to "0", not an empty field
    assert parse_receipts.norm_locator("0000", "0000", "000000", "06/20/26") == \
        "0000|0|0|06/20/26"


# --- money -----------------------------------------------------------------

def test_money_parses_plain_amount():
    assert parse_receipts.money("122.05") == 122.05


def test_money_strips_dollar_sign_and_thousands_commas():
    assert parse_receipts.money("$1,234.56") == 1234.56


def test_money_handles_negative_amount():
    assert parse_receipts.money("-$122.05") == -122.05


def test_money_returns_none_for_unparseable_input():
    assert parse_receipts.money("N/A") is None


# --- html_to_text ------------------------------------------------------------

def test_html_to_text_strips_tags():
    html = "<div>PO/JOB NAME: 456 SAMPLE AVE</div>"
    assert parse_receipts.html_to_text(html) == "PO/JOB NAME: 456 SAMPLE AVE"


def test_html_to_text_decodes_entities():
    html = "<p>TOTAL&nbsp;&nbsp;&#36;122.05 &amp; tax &lt;incl&gt; &quot;ok&quot;</p>"
    assert parse_receipts.html_to_text(html) == 'TOTAL $122.05 & tax <incl> "ok"'


def test_html_to_text_drops_script_and_style_blocks():
    html = (
        "<style>.a{color:red}</style>"
        "<script>var x = 1;</script>"
        "<div>KEEP ME</div>"
    )
    assert parse_receipts.html_to_text(html) == "KEEP ME"


def test_html_to_text_collapses_blank_lines_and_inner_whitespace():
    html = "<div>A</div>\n\n<div>   B    C   </div>\n<div></div>"
    assert parse_receipts.html_to_text(html) == "A\nB C"


# --- _dedupe -----------------------------------------------------------------

def test_dedupe_removes_duplicates_keeping_first_occurrence_order():
    items = [{"v": 1}, {"v": 2}, {"v": 1}, {"v": 3}, {"v": 2}]
    out = parse_receipts._dedupe(items, lambda it: it["v"])
    assert out == [{"v": 1}, {"v": 2}, {"v": 3}]


def test_dedupe_empty_list_returns_empty_list():
    assert parse_receipts._dedupe([], lambda it: it) == []
