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
