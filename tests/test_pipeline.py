#!/usr/bin/env python3
"""
End-to-end pipeline tests: run parse_receipts.py and build_ledger.py as real
subprocesses against examples/ and check the outputs.

Requirements: `pip install pytest` (the only dependency; the pipeline scripts
themselves stay stdlib-only). The sample receipts under examples/ are HTML, so
these tests do NOT need `pdftotext`/Poppler installed.

Run with: pytest -q
"""
import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES_DIR = REPO_ROOT / "examples"
SRC_DIR = REPO_ROOT / "src"
ORDERHISTORY = EXAMPLES_DIR / "sample_orderhistory.json"

LEDGER_COLUMNS = [
    "date", "type", "origin", "store", "project", "total", "pretax",
    "transaction_id", "receipt_locator", "invoices", "tenders",
    "store_credit_amt", "store_credit_acct", "needs_review", "review_reason",
]


def run(args, cwd=REPO_ROOT):
    return subprocess.run(
        [sys.executable, *args], cwd=cwd, capture_output=True, text=True,
    )


@pytest.fixture(scope="module")
def parsed_receipts(tmp_path_factory):
    """Run parse_receipts.py on examples/ once and share the result across tests."""
    outdir = tmp_path_factory.mktemp("parsed")
    result = run([str(SRC_DIR / "parse_receipts.py"), str(EXAMPLES_DIR), "-o", str(outdir)])
    assert result.returncode == 0, result.stderr
    recs = json.loads((outdir / "receipts.json").read_text())
    return {"outdir": outdir, "recs": recs}


def _sale_and_return(recs):
    sale = next(r for r in recs if not r.get("is_return"))
    ret = next(r for r in recs if r.get("is_return"))
    return sale, ret


# --- parse_receipts.py -------------------------------------------------------

def test_parse_receipts_produces_two_receipts(parsed_receipts):
    assert len(parsed_receipts["recs"]) == 2


def test_sale_receipt_parsed_correctly(parsed_receipts):
    sale, _ = _sale_and_return(parsed_receipts["recs"])
    assert sale["project"] == "456 SAMPLE AVE"
    assert sale["total"] == 122.05
    assert sale["is_return"] is False
    assert sale["tenders"] == [{"last4": "0000", "type": "AMEX", "amount": 122.05}]


def test_return_receipt_resolves_project_via_orig_rec(parsed_receipts):
    _, ret = _sale_and_return(parsed_receipts["recs"])
    assert ret["project"] is None            # no PO/JOB NAME on the return itself
    assert ret["total"] == -122.05
    assert ret["resolved_projects"] == ["456 SAMPLE AVE"]
    assert ret["unresolved_orig_recs"] == []
    tenders = {(t["last4"], t["type"], t["amount"]) for t in ret["tenders"]}
    assert tenders == {
        ("0000", "AMEX", 48.26),
        ("4444", "AMEX", 26.27),
        ("2222", "STORE CREDIT", 47.52),
    }


def test_sale_line_items_captured(parsed_receipts):
    sale, _ = _sale_and_return(parsed_receipts["recs"])
    items = sale["line_items"]
    assert len(items) == 2
    by_desc = {i["description"]: i for i in items}
    assert by_desc["FICTIONAL 2X4 STUD 8FT"]["amount"] == 38.90
    assert by_desc["SAMPLE INTERIOR PAINT GAL"]["amount"] == 76.10
    assert all(i["orig_rec"] is None for i in items)   # a sale's items have no ORIG REC


def test_return_line_items_carry_orig_rec(parsed_receipts):
    _, ret = _sale_and_return(parsed_receipts["recs"])
    items = ret["line_items"]
    assert len(items) == 2
    # every returned item is grouped under the original sale's locator
    assert {i["orig_rec"] for i in items} == {"0000|52|71234|06/20/26"}


def test_tender_roster_includes_store_credit_card(parsed_receipts):
    with open(parsed_receipts["outdir"] / "tender_roster.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    row = next(r for r in rows if r["last4"] == "2222")
    assert row["detected_type"] == "STORE CREDIT"
    assert row["is_store_credit"] == "YES"


# --- build_ledger.py ----------------------------------------------------------

def test_build_ledger_with_receipts_resolves_return(tmp_path, parsed_receipts):
    receipts_json = parsed_receipts["outdir"] / "receipts.json"
    ledger_path = tmp_path / "ledger.csv"
    result = run([
        str(SRC_DIR / "build_ledger.py"),
        "--orderhistory", str(ORDERHISTORY),
        "--receipts", str(receipts_json),
        "-o", str(ledger_path),
    ])
    assert result.returncode == 0, result.stderr

    with open(ledger_path, newline="") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == LEDGER_COLUMNS
        by_date = {r["date"]: r for r in reader}

    ret_row = by_date["2026-06-28"]
    assert ret_row["type"] == "Return"
    assert ret_row["project"] == "456 SAMPLE AVE (via receipt)"
    assert ret_row["needs_review"] == ""
    assert ret_row["review_reason"] == ""
    assert ret_row["store_credit_amt"] == "47.52"
    assert ret_row["store_credit_acct"] == "HD Store Credit"

    sale_row = by_date["2026-06-29"]
    assert sale_row["store_credit_amt"] == "47.52"
    assert sale_row["store_credit_acct"] == "HD Store Credit"

    cancel_row = by_date["2026-06-27"]
    assert cancel_row["type"] == "Cancel"
    assert cancel_row["needs_review"] == "YES"
    assert cancel_row["review_reason"] == "cancellation"


def test_build_ledger_without_receipts_flags_return_for_review(tmp_path):
    ledger_path = tmp_path / "ledger_noreceipts.csv"
    # Isolate cwd to tmp_path: with no --receipts, build_ledger.py falls back to
    # looking for ./receipts.json, and this must NOT find one.
    result = run([
        str(SRC_DIR / "build_ledger.py"),
        "--orderhistory", str(ORDERHISTORY),
        "-o", str(ledger_path),
    ], cwd=tmp_path)
    assert result.returncode == 0, result.stderr

    with open(ledger_path, newline="") as f:
        rows = list(csv.DictReader(f))
    ret_row = next(r for r in rows if r["date"] == "2026-06-28")
    assert ret_row["project"] == ""
    assert ret_row["needs_review"] == "YES"
    assert ret_row["review_reason"] == "return-project-unknown"

    cancel_row = next(r for r in rows if r["date"] == "2026-06-27")
    assert cancel_row["needs_review"] == "YES"
    assert cancel_row["review_reason"] == "cancellation"

    # a return with no resolvable project also lands in the lookup worklist
    lookup = tmp_path / "returns_needing_lookup.csv"
    assert lookup.exists()
    with open(lookup, newline="") as f:
        wl = list(csv.DictReader(f))
    assert any(r["date"] == "2026-06-28" for r in wl)


def test_return_resolves_via_order_history_spine(tmp_path):
    """Ladder rung 2: the original sale is only in the order-history spine (its receipt
    was never emailed), but the return still recovers its project via the ORIG REC on
    the parsed return receipt. This is the shape of the 08/14/25 shower-faucet case."""
    orderhistory = {
        "pulled": 2,
        "rows": [
            {   # original sale — present ONLY in the spine, not in any receipt corpus
                "date": "2025-08-14", "type": "Sale", "origin": "#8119, Faraway", "store": 8119,
                "job": "2708 WILLOW GLEN", "total": 193.41, "pretax": 182.46, "tx": 5001,
                "receipt": "00015-58022", "invoices": [],
                "tenders": [{"net": "AX", "last4": "1018", "amt": "-193.41"}],
            },
            {   # the return, months later, with a blank job
                "date": "2026-07-14", "type": "Return", "origin": "#2584, Town", "store": 2584,
                "job": "", "total": -193.41, "pretax": -182.46, "tx": 5673,
                "receipt": "00018-56731", "invoices": [],
                "tenders": [{"net": "AX", "last4": "1018", "amt": "193.41"}],
            },
        ],
    }
    receipts = [{
        "file": "return.pdf", "format": "register", "is_return": True,
        "datetime": "07/14/26 02:52 PM", "total": -193.41,
        "project": None, "orig_recs": ["8119|15|58022|08/14/25"],
        "resolved_projects": [], "unresolved_orig_recs": ["8119|15|58022|08/14/25"],
        "tenders": [{"last4": "1018", "type": "AMEX", "amount": 193.41}],
        "line_items": [],
    }]
    oh_path = tmp_path / "oh.json"
    rc_path = tmp_path / "receipts.json"
    oh_path.write_text(json.dumps(orderhistory))
    rc_path.write_text(json.dumps(receipts))
    ledger_path = tmp_path / "ledger.csv"
    result = run([
        str(SRC_DIR / "build_ledger.py"),
        "--orderhistory", str(oh_path), "--receipts", str(rc_path), "-o", str(ledger_path),
    ])
    assert result.returncode == 0, result.stderr
    with open(ledger_path, newline="") as f:
        rows = {r["date"]: r for r in csv.DictReader(f)}
    ret = rows["2026-07-14"]
    assert ret["project"] == "2708 WILLOW GLEN (via order-history)"
    assert ret["needs_review"] == ""
