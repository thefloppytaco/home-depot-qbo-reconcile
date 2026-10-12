#!/usr/bin/env python3
"""
Weekly-sync tests: merge_orderhistory.py -> build_ledger.py -> gen_entries.py -> entries.py (hdlib) -> closeout.py
-> audit_job.py, run as real subprocesses in a throwaway workspace built from examples/weekly-sync/ (synthetic).
Plus unit tests of the rules that matter most: job resolution, ORIG REC allocation, store-credit allocation, the
tie checks, and the merge's stop-on-vanished-row guard.

Run with: pytest -q
"""
import csv
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
SAMPLES = REPO / "examples" / "weekly-sync"
SYNC = "sync/2026-10-12"
sys.path.insert(0, str(SRC))

import hdlib  # noqa: E402
from gen_entries import Gen, read_bundles  # noqa: E402

CONFIG = json.loads((SAMPLES / "sample_hd-sync.json").read_text())
CONFIG = {k: v for k, v in CONFIG.items() if not k.startswith("_")}


def run(args, cwd):
    return subprocess.run([sys.executable, *map(str, args)], cwd=cwd, capture_output=True, text=True)


def make_workspace(root: Path) -> Path:
    (root / SYNC).mkdir(parents=True)
    for src, dst in [("sample_hd-sync.json", "hd-sync.json"), ("sample_history.json", "hd_orderhistory_full.json"),
                     ("sample_line_items.csv", "line_items.csv"), ("sample_hd_sync_state.json", "hd_sync_state.json"),
                     ("sample_bundles.jsonl", f"{SYNC}/hd_bundles_2026-10-12.jsonl")]:
        shutil.copy(SAMPLES / src, root / dst)
    return root


@pytest.fixture(scope="module")
def week(tmp_path_factory):
    """One full synthetic week, run once and shared."""
    ws = make_workspace(tmp_path_factory.mktemp("ws"))
    steps = {}
    steps["merge"] = run([SRC / "merge_orderhistory.py", "--sync", SYNC, "--write"], ws)
    steps["ledger"] = run([SRC / "build_ledger.py"], ws)
    steps["gen"] = run([SRC / "gen_entries.py", "--sync", SYNC, "--only-new"], ws)
    steps["entries"] = run(["entries.py"], ws / SYNC)
    return ws, steps


def test_every_step_succeeds(week):
    ws, steps = week
    for name, r in steps.items():
        assert r.returncode == 0, f"{name} failed:\n{r.stdout}\n{r.stderr}"


def test_merge_finds_only_the_new_rows(week):
    ws, steps = week
    assert "matched: 1 | new: 8 | vanished: 0" in steps["merge"].stdout
    spine = json.loads((ws / "hd_orderhistory_full.json").read_text())
    assert len(spine["rows"]) == 12
    assert list(ws.glob("hd_orderhistory_full.backup-*.json")), "merge must back up the history before writing"


def test_every_new_row_gets_a_bucket(week):
    ws, _ = week
    rows = list(csv.DictReader(open(ws / SYNC / "new_rows.csv")))
    buckets = sorted(r["bucket"] for r in rows)
    assert buckets == ["cancel", "not-posted", "proposed", "proposed", "proposed", "proposed", "proposed", "sub-row"]
    assert "BLANK" not in (ws / SYNC / "PROPOSAL.md").read_text()


def entries(ws):
    return {e["key"]: e for e in json.loads((ws / SYNC / "entries.json").read_text())}


def test_batch_return_is_allocated_by_orig_rec_not_by_its_po(week):
    """The 10/08 return row carries 456 SAMPLE AVE's PO; one of its two originals was booked to 123 Example St."""
    ws, _ = week
    j1 = entries(ws)["J1"]
    assert j1["type"] == "Return (SC earn)" and j1["amount"] == 84.80
    alloc = {g[0]: round(sum(i[3] for i in g[4]) + g[5], 2) for g in j1["groups"]}
    assert alloc == {"123 Example St": 37.10, "456 Sample Ave": 47.70}
    je = json.loads((ws / SYNC / "payloads.json").read_text())["J1"]
    assert je["tool"] == "create_journal_entry"
    lines = je["params"]["journalEntry"]["Line"]
    debit = [l for l in lines if l["JournalEntryLineDetail"]["PostingType"] == "Debit"]
    credits = {l["JournalEntryLineDetail"]["Entity"]["EntityRef"]["value"]: l["Amount"] for l in lines
               if l["JournalEntryLineDetail"]["PostingType"] == "Credit"}
    assert debit[0]["JournalEntryLineDetail"]["AccountRef"]["value"] == CONFIG["accounts"]["store_credit"]
    assert credits == {"201": 37.10, "202": 47.70}


def test_card_return_posts_as_a_credit_on_the_original_job(week):
    ws, _ = week
    c1 = json.loads((ws / SYNC / "payloads.json").read_text())["C1"]["params"]["purchase"]
    assert c1["Credit"] is True
    assert c1["AccountRef"]["value"] == CONFIG["cards"]["1111"]
    assert {l["AccountBasedExpenseLineDetail"]["CustomerRef"]["value"] for l in c1["Line"]} == {"201"}
    assert round(sum(l["Amount"] for l in c1["Line"]), 2) == 31.80


def test_split_tender_store_credit_in_receipt_order_card_carries_tax(week):
    ws, _ = week
    e = entries(ws)
    s1, p2 = e["S1"], e["P2"]
    assert s1["card"] == "SC ..2222" and s1["amount"] == 50.00
    assert [round(i[3], 2) for i in s1["items"]] == [40.00, 10.00]      # studs whole, screws split
    assert p2["amount"] == 45.40 and p2["items"][-1][0] == "Sales tax"
    assert all(i[0] != "Sales tax" for i in s1["items"])
    assert p2["job"] == "456 Sample Ave"                                 # PO "OFFICE" via po_aliases
    pu = json.loads((ws / SYNC / "payloads.json").read_text())["S1"]["params"]["purchase"]
    assert pu["PaymentType"] == "Cash" and pu["AccountRef"]["value"] == CONFIG["accounts"]["store_credit"]


def test_never_book_card_is_itemized_not_posted(week):
    ws, _ = week
    assert entries(ws)["X1"]["book"] is False
    assert "X1" not in json.loads((ws / SYNC / "payloads.json").read_text())
    items = list(csv.DictReader(open(ws / SYNC / "line_items_new.csv")))
    assert {r["qbo_id"] for r in items if r["receipt_locator"] == "00051-70017"} == {"Visa ..9999 is on the never_book list"}


def test_net_price_per_line_and_derived_fee(week):
    ws, steps = week
    e = entries(ws)
    door = e["P1"]["items"][0]
    assert door[3] == 388.00 and "list 400.00" in door[0]                # net booked, list noted
    p3 = e["P3"]
    assert p3["job"] == "456 Sample Ave"                                 # PO from the Pro Desk order PDF
    assert any(i[0].startswith("Delivery fee") and i[3] == 12.00 for i in p3["items"])
    assert "CHECK: fee line" in (ws / SYNC / "entries.py").read_text()


def test_closeout_needs_ids_only_for_posted_entries(week, tmp_path):
    ws = make_workspace(tmp_path)
    for args, cwd in [([SRC / "merge_orderhistory.py", "--sync", SYNC, "--write"], ws), ([SRC / "build_ledger.py"], ws),
                      ([SRC / "gen_entries.py", "--sync", SYNC, "--only-new"], ws), (["entries.py"], ws / SYNC)]:
        assert run(args, cwd).returncode == 0
    r = run([SRC / "closeout.py", "--sync", SYNC, "--ids", "P1=1,P2=2,P3=3,S1=4,J1=5"], ws)
    assert r.returncode != 0 and "C1" in (r.stdout + r.stderr)
    before = (ws / "line_items.csv").read_text().count("\n")
    r = run([SRC / "closeout.py", "--sync", SYNC, "--ids", "P1=1,P2=2,P3=3,S1=4,J1=5,C1=6", "--write"], ws)
    assert r.returncode == 0, r.stdout + r.stderr
    after = (ws / "line_items.csv").read_text().count("\n")
    assert after - before == 19
    state = json.loads((ws / "hd_sync_state.json").read_text())
    assert state["next_window_after"] == "2026-10-10" and state["posted_ids"]["J1"] == "5"
    assert list(ws.glob("booking_log_*.csv"))


def test_audit_credits_cross_job_returns_to_the_right_job(week, tmp_path):
    ws, _ = week
    r = run([SRC / "audit_job.py", "--job", "123 Example St", "--out", tmp_path], ws)
    assert r.returncode == 0, r.stdout + r.stderr
    rows = list(csv.DictReader(open(tmp_path / "audit_123_example_st.csv")))
    ret = {(x["receipt"], round(float(x["amount"]), 2)) for x in rows if x["type"] == "Return"}
    assert ret == {("00050-80015", -37.10), ("00050-90012", -31.80)}
    assert round(sum(float(x["amount"]) for x in rows), 2) == 554.38
    r = run([SRC / "audit_job.py", "--job", "456 Sample Ave", "--out", tmp_path], ws)
    rows = list(csv.DictReader(open(tmp_path / "audit_456_sample_ave.csv")))
    assert ("00050-80015", -47.70) in {(x["receipt"], round(float(x["amount"]), 2)) for x in rows}
    assert not any(x["receipt"] == "00051-70017" and x["type"] == "Sale" and float(x["amount"]) == 0 for x in rows)


def test_merge_stops_when_a_stored_row_vanishes(tmp_path):
    ws = make_workspace(tmp_path)
    spine = json.loads((ws / "hd_orderhistory_full.json").read_text())
    spine["rows"].append(dict(spine["rows"][0], tx=9998, txc=99981, receipt="00050-99981", total=1.23))
    (ws / "hd_orderhistory_full.json").write_text(json.dumps(spine))
    r = run([SRC / "merge_orderhistory.py", "--sync", SYNC, "--write"], ws)
    assert r.returncode == 2 and "VANISHED" in r.stdout
    assert not (ws / SYNC / "new_rows.csv").exists()
    assert json.loads((ws / "hd_orderhistory_full.json").read_text()) == spine


def test_merge_refuses_a_job_filtered_pull(tmp_path):
    ws = make_workspace(tmp_path)
    f = ws / SYNC / "hd_bundles_2026-10-12.jsonl"
    lines = f.read_text().splitlines()
    meta = json.loads(lines[0])
    meta["_meta"]["job"] = "123 Example"
    f.write_text("\n".join([json.dumps(meta)] + lines[1:]) + "\n")
    r = run([SRC / "merge_orderhistory.py", "--sync", SYNC, "--write"], ws)
    assert r.returncode != 0 and "job-filtered" in (r.stdout + r.stderr)


# ---------------------------------------------------------------- unit tests

def gen(config=None):
    return Gen(config or CONFIG, [], [], [])


@pytest.mark.parametrize("po, job", [
    ("123 EXAMPLE STREET", "123 Example St"),
    ("123 example st.", "123 Example St"),
    ("OFFICE", "456 Sample Ave"),
    ("office supplies", "456 Sample Ave"),
    ("456 SAMPEL AVE", "456 Sample Ave"),      # spelling: same number, first four letters
    ("TOOLS", "Tools"),
])
def test_resolve_job(po, job):
    assert gen().resolve_job(po)[0] == job


@pytest.mark.parametrize("po", ["", "0", "NA", "999 Nowhere Rd"])
def test_resolve_job_refuses_to_guess(po):
    job, flag = gen().resolve_job(po)
    assert job == "" and flag.startswith("REVIEW")


def test_resolve_job_flags_ambiguity():
    cfg = dict(CONFIG, jobs={"123 Example St": "201", "123 Example St Unit B": "203"})
    job, flag = gen(cfg).resolve_job("123 EXAMPLE")
    assert job == "" and "several jobs" in flag


def test_card_status():
    g = gen(dict(CONFIG, ask_first=["5555"]))
    assert g.card_status("GR", "2222") == "sc"
    assert g.card_status("VI", "9999") == "never"
    assert g.card_status("VI", "5555") == "ask"
    assert g.card_status("VI", "1111") == "ok"
    assert g.card_status("MA", "3333") == "unknown"


def test_read_bundles_skips_meta_and_fills_failed_rows(tmp_path):
    f = tmp_path / "b.jsonl"
    row = {"date": "2026-10-01", "type": "Sale", "store": 0, "register": 1, "txc": 10, "tx": 1, "job": "X", "total": 1.06, "pretax": 1.0,
           "order": "", "receipt": "00001-00010", "tenders": [{"net": "VI", "last4": "1111", "amt": "-1.06"}]}
    f.write_text(json.dumps({"_meta": {"from": "2026-10-01"}}) + "\n" + json.dumps({"i": 0, "err": "HTTP 500", "row": row}) + "\n")
    b = read_bundles(str(f))
    assert len(b) == 1 and b[0]["d"] == "2026-10-01" and b[0]["tn"] == [["VI", "1111", "-1.06"]]


@pytest.fixture
def lib():
    hdlib.reset(dict(CONFIG))
    yield hdlib
    hdlib.reset()


def test_ties_catch_bad_arithmetic(lib, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    lib.add("P1", "2026-10-01", "00001-00010", "", 0, "123 Example St", "Visa ..1111", "Sale", 106.00,
            [("Thing", 2, 50.00, 99.00), (lib.T, 1, 6.00, 6.00)])
    bad = lib.ties()
    assert any("qty x unit" in str(b) for b in bad) and any("lines != amount" in str(b) for b in bad)


def test_store_credit_refund_requires_groups(lib, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    lib.add("J1", "2026-10-01", "00001-00010", "", 0, "123 Example St", "SC ..2222", "Return (SC earn)", 10.60,
            [("Thing", 1, 10.00, 10.00), (lib.T, 1, 0.60, 0.60)])
    assert any("needs groups" in str(b) for b in lib.ties())


def test_unresolved_group_blocks_finish(lib, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    lib.add("J1", "2026-10-01", "00001-00010", "", 0, "return by ORIG REC", "SC ..2222", "Return (SC earn)", 10.60, [],
            groups=[("REVIEW", None, "ORIG REC 0000 001 00010 09/01/26", "", [("Thing", 1, 10.00, 10.00)], 0.60)])
    assert any("unresolved group" in str(b) for b in lib.ties())
    with pytest.raises(SystemExit):
        lib.finish()
    assert not (tmp_path / "payloads.json").exists()


def test_never_book_card_cannot_be_posted(lib):
    with pytest.raises(SystemExit):
        lib.acct_of("Visa ..9999")


def test_unknown_job_is_refused_not_guessed(lib):
    with pytest.raises(SystemExit):
        lib.cust_of("999 Nowhere Rd")
    assert lib.cust_of("Tools") is None and lib.cust_of("untagged (NO RECEIPT)") is None
