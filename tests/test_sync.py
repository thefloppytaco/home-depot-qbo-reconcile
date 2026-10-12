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
    assert "matched: 1 | new: 11 | vanished: 0" in steps["merge"].stdout
    spine = json.loads((ws / "hd_orderhistory_full.json").read_text())
    assert len(spine["rows"]) == 18
    assert list(ws.glob("hd_orderhistory_full.backup-*.json")), "merge must back up the history before writing"


def test_every_new_row_gets_a_bucket(week):
    ws, _ = week
    rows = list(csv.DictReader(open(ws / SYNC / "new_rows.csv")))
    buckets = sorted(r["bucket"] for r in rows)
    assert buckets == ["cancel", "not-posted"] + ["proposed"] * 7 + ["sub-row"] * 2
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
    r = run([SRC / "closeout.py", "--sync", SYNC, "--ids", "P1=1,P2=2,P3=3,S1=4,J1=5,C2=7,C3=8"], ws)
    assert r.returncode != 0 and "C1" in (r.stdout + r.stderr)
    before = (ws / "line_items.csv").read_text().count("\n")
    r = run([SRC / "closeout.py", "--sync", SYNC, "--ids", "P1=1,P2=2,P3=3,S1=4,J1=5,C1=6,C2=7,C3=8", "--write"], ws)
    assert r.returncode == 0, r.stdout + r.stderr
    after = (ws / "line_items.csv").read_text().count("\n")
    assert after - before == 25
    state = json.loads((ws / "hd_sync_state.json").read_text())
    assert state["next_window_after"] == "2026-10-12" and state["posted_ids"]["J1"] == "5"
    assert list(ws.glob("booking_log_*.csv"))


def test_audit_credits_cross_job_returns_to_the_right_job(week, tmp_path):
    ws, _ = week
    r = run([SRC / "audit_job.py", "--job", "123 Example St", "--out", tmp_path], ws)
    assert r.returncode == 0, r.stdout + r.stderr
    rows = list(csv.DictReader(open(tmp_path / "audit_123_example_st.csv")))
    ret = {(x["receipt"], round(float(x["amount"]), 2)) for x in rows if x["type"] == "Return"}
    assert ret == {("00050-80015", -37.10), ("00050-90012", -31.80), ("00016-58640", -31.76)}
    assert round(sum(float(x["amount"]) for x in rows), 2) == 554.38
    assert ("00016-58640", -31.76) in {(x["receipt"], round(float(x["amount"]), 2)) for x in rows}   # online item, via order number
    r = run([SRC / "audit_job.py", "--job", "456 Sample Ave", "--out", tmp_path], ws)
    rows = list(csv.DictReader(open(tmp_path / "audit_456_sample_ave.csv")))
    got = [(x["receipt"], round(float(x["amount"]), 2)) for x in rows]
    assert ("00050-80015", -47.70) in got and ("00016-58640", -53.00) in got and ("00019-23903", -53.00) in got
    assert ("00016-58640", -31.76) not in got
    assert not any(x["receipt"] == "00051-70017" and x["type"] == "Sale" and float(x["amount"]) == 0 for x in rows)


def test_order_return_row_on_the_same_receipt_is_not_a_second_credit(week):
    """One register return, two Home Depot rows: the whole receipt and an order-return row repeating part of it."""
    ws, _ = week
    rows = [r for r in csv.DictReader(open(ws / SYNC / "new_rows.csv")) if r["receipt"] == "00016-58640"]
    assert sorted((r["total"], r["bucket"], r["subrow_of"]) for r in rows) == [("-31.76", "sub-row", "00016-58640"), ("-84.76", "proposed", "")]
    e = entries(ws)
    assert [k for k, v in e.items() if v["loc"] == "00016-58640"] == ["C2"]
    alloc = {g[0]: (round(sum(i[3] for i in g[4]) + g[5], 2), g[3]) for g in e["C2"]["groups"]}
    assert alloc == {"456 Sample Ave": (53.00, "#5015"), "123 Example St": (31.76, "#5020")}   # online item found by order number


def test_missing_block_tax_is_derived_from_the_receipt(week):
    ws, _ = week
    c3 = entries(ws)["C3"]
    assert c3["groups"][0][0] == "456 Sample Ave" and c3["groups"][0][5] == 3.00
    assert "derived from the receipt total" in (ws / SYNC / "entries.py").read_text()


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


def test_partition_gives_each_group_to_its_leg_only_when_unique():
    legs = [("GR", "2222", 2.83), ("AX", "0000", 7.12)]
    assert Gen.partition(legs, [2.83, 7.12]) == (0, 1)
    assert Gen.partition(legs, [7.12, 2.83]) == (1, 0)
    assert Gen.partition([("GR", "2222", 5.00), ("AX", "0000", 5.00)], [5.00, 5.00]) is None   # two ways - ask
    assert Gen.partition([("GR", "2222", 122.38), ("AX", "0000", 175.20)], [265.82, 31.76]) is None


def _sale_bundle(tn, items, pre, tot, po="123 EXAMPLE ST"):
    return {"i": 0, "d": "2026-10-01", "t": "Sale", "s": 0, "reg": 1, "txc": 10, "tx": 1, "po_row": po, "tot": tot, "pre": pre, "on": "",
            "tn": tn, "od": {"items": items, "head": [0, 1, 10, "2026-10-01"], "po_online": po}, "rc": {"printedPO": po, "blocks": [], "balances": []}}


def _run_draft(bundles, tmp_path, monkeypatch):
    out, _ = gen().run(bundles)
    src = "\n".join(out).replace("from hdlib import *", "from hdlib import *\nreset(CFG)")
    monkeypatch.chdir(tmp_path)
    ns = {"CFG": dict(CONFIG), "__file__": str(tmp_path / "entries.py")}
    exec(compile(src, "entries.py", "exec"), ns)
    return {e["key"]: e for e in hdlib.E}, src


def test_store_credit_only_sale_pays_the_tax_too(tmp_path, monkeypatch):
    b = _sale_bundle([["GR", "2222", "-53.00"]], [{"sku": "1", "desc": "Caulk", "qty": 1, "net": 50.00, "pretax": 50.00}], 50.00, 53.00)
    E, _ = _run_draft([b], tmp_path, monkeypatch)
    assert E["S1"]["amount"] == 53.00 and E["S1"]["items"][-1][0] == "Sales tax"
    hdlib.reset()


def test_multi_leg_return_split_by_unique_partition_and_same_job(tmp_path, monkeypatch):
    def ret(txc, tn, blocks, tot, pre):
        return {"i": txc, "d": "2026-10-09", "t": "Return", "s": 0, "reg": 50, "txc": txc, "tx": txc // 10, "po_row": "", "tot": tot, "pre": pre, "on": "",
                "tn": tn, "od": {}, "rc": {"printedPO": "", "balances": [], "blocks": blocks}}
    blk = lambda ref, amt, tax: {"ref": ref, "tax": -tax, "items": [{"desc": "ITEM", "qty": 1, "unit": -amt, "total": -amt}]}
    ledger = [{"store": "0", "date": "2026-09-18", "transaction_id": "4674", "receipt_locator": "00050-46743", "project": "123 EXAMPLE ST", "tenders": "Visa..1111=-106.00", "total": "106.00", "type": "Sale"},
                {"store": "0", "date": "2026-09-20", "transaction_id": "5512", "receipt_locator": "00052-55128", "project": "456 SAMPLE AVE", "tenders": "Amex..0000=-63.60", "total": "63.60", "type": "Sale"}]
    g2 = Gen(CONFIG, ledger, [], [])
    split = ret(10012, [["GR", "2222", "2.83"], ["AX", "0000", "7.12"]],
                [blk(["0000", "050", "46743", "09/18/26"], 2.67, 0.16), blk(["0000", "052", "55128", "09/21/26"], 6.72, 0.40)], -9.95, -9.39)
    same = ret(20014, [["GR", "2222", "40.00"], ["AX", "0000", "66.00"]],
               [blk(["0000", "050", "46743", "09/18/26"], 60.00, 3.60), blk(["0000", "050", "46743", "09/18/26"], 40.00, 2.40)], -106.00, -100.00)
    out, _ = g2.run([split, same])
    src = "\n".join(out).replace("from hdlib import *", "from hdlib import *\nreset(CFG)")
    monkeypatch.chdir(tmp_path)
    exec(compile(src, "entries.py", "exec"), {"CFG": dict(CONFIG), "__file__": str(tmp_path / "entries.py")})
    E = {e["key"]: e for e in hdlib.E}
    # 9/21 printed vs 9/20 in the history: found within 3 days; GR 2.83 = the 123 Example St group, AX 7.12 = the 456 Sample Ave group
    assert [g[0] for g in E["J1"]["groups"]] == ["123 Example St"] and E["J1"]["amount"] == 2.83
    assert [g[0] for g in E["C1"]["groups"]] == ["456 Sample Ave"] and E["C1"]["amount"] == 7.12
    # both groups on one job: each leg gets its share
    assert E["J2"]["amount"] == 40.00 and E["C2"]["amount"] == 66.00
    assert {g[0] for k in ("J2", "C2") for g in E[k]["groups"]} == {"123 Example St"}
    assert not hdlib.ties()
    hdlib.reset()


# ---------------------------------------------------------------- first sync, holds, hand entry, config

def test_first_sync_after_setup_lists_stored_rows_not_yet_booked(tmp_path):
    """Setup's full-history pull already stored every row since the cutoff; the first sync must still book them."""
    ws = make_workspace(tmp_path)
    (ws / "hd_sync_state.json").unlink()                                   # never synced
    spine = json.loads((ws / "hd_orderhistory_full.json").read_text())
    have = {(r["date"], r["receipt"], r["total"]) for r in spine["rows"]}
    for line in (ws / SYNC / "hd_bundles_2026-10-12.jsonl").read_text().splitlines()[1:]:
        row = json.loads(line)["row"]
        if (row["date"], row["receipt"], row["total"]) not in have:
            spine["rows"].append(row)                                      # "setup" pulled everything
    (ws / "hd_orderhistory_full.json").write_text(json.dumps(spine))
    cfg = json.loads((ws / "hd-sync.json").read_text())
    cfg["cutoff_date"] = "2026-10-01"
    (ws / "hd-sync.json").write_text(json.dumps(cfg))
    r = run([SRC / "merge_orderhistory.py", "--sync", SYNC, "--write"], ws)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "new: 0" in r.stdout and "stored but not booked yet" in r.stdout
    rows = list(csv.DictReader(open(ws / SYNC / "new_rows.csv")))
    assert {x["receipt"] for x in rows} >= {"00090-50006", "00016-58640", "00019-23903"}
    assert "00050-12340" not in {x["receipt"] for x in rows}                # 10/02 was already booked
    assert run([SRC / "build_ledger.py"], ws).returncode == 0
    assert run([SRC / "gen_entries.py", "--sync", SYNC, "--only-new"], ws).returncode == 0
    r = run(["entries.py"], ws / SYNC)
    assert r.returncode == 0 and "OK: 9 entries" in r.stdout, r.stdout + r.stderr


def test_a_held_receipt_comes_back_next_week(tmp_path):
    ws = make_workspace(tmp_path)
    for args, cwd in [([SRC / "merge_orderhistory.py", "--sync", SYNC, "--write"], ws), ([SRC / "build_ledger.py"], ws),
                      ([SRC / "gen_entries.py", "--sync", SYNC, "--only-new"], ws)]:
        assert run(args, cwd).returncode == 0
    draft = (ws / SYNC / "entries.py").read_text()
    start = draft.index("add('P3'")
    end = draft.index("note=\"printed PO '456 Sample Ave'\")", start) + len("note=\"printed PO '456 Sample Ave'\")")
    # deleting an entry is refused...
    (ws / SYNC / "entries.py").write_text(draft[:start] + draft[end:])
    r = run(["entries.py"], ws / SYNC)
    assert r.returncode != 0 and "hold(" in r.stdout
    # ...holding it is not
    (ws / SYNC / "entries.py").write_text(draft[:start] + "hold('00077-10106', '2026-10-10', 'which job is the vanity for?', order='H0000-123456')" + draft[end:])
    r = run(["entries.py"], ws / SYNC)
    assert r.returncode == 0 and "1 held" in r.stdout, r.stdout + r.stderr
    assert "HELD - which job is the vanity for?" in (ws / SYNC / "PROPOSAL.md").read_text()
    r = run([SRC / "closeout.py", "--sync", SYNC, "--manual", "--write"], ws)
    assert r.returncode == 0, r.stdout + r.stderr
    state = json.loads((ws / "hd_sync_state.json").read_text())
    assert state["held"][0]["receipt"] == "00077-10106" and state["posted_ids"]["P1"] == "manual-" + state["last_sync"] + "-P1"
    assert state["open_cancellations"][0]["order"] == "H0000-123999"
    # next week: the same fetch file again (zone covers 10/10) - the held receipt is listed again, nothing else is
    nxt = ws / "sync" / "2026-10-19"
    nxt.mkdir()
    shutil.copy(ws / SYNC / "hd_bundles_2026-10-12.jsonl", nxt / "hd_bundles_2026-10-19.jsonl")
    r = run([SRC / "merge_orderhistory.py", "--sync", "sync/2026-10-19", "--write"], ws)
    assert r.returncode == 0, r.stdout + r.stderr
    rows = list(csv.DictReader(open(nxt / "new_rows.csv")))
    assert [(x["receipt"], x["notes"]) for x in rows] == [("00077-10106", "held last sync")]


def test_merge_refuses_a_pull_that_starts_after_last_window(tmp_path):
    ws = make_workspace(tmp_path)
    (ws / "hd_sync_state.json").write_text(json.dumps({"next_window_after": "2026-09-20"}))
    r = run([SRC / "merge_orderhistory.py", "--sync", SYNC], ws)
    assert r.returncode != 0 and "would be missed" in (r.stdout + r.stderr)


def test_entries_before_the_cutoff_are_refused(lib, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    lib.reset(dict(CONFIG, cutoff_date="2026-10-01"))
    lib.add("P1", "2026-09-30", "00001-00010", "", 0, "123 Example St", "Visa ..1111", "Sale", 10.60, [("Thing", 1, 10.00, 10.00), (lib.T, 1, 0.60, 0.60)])
    assert any("before cutoff_date" in str(b) for b in lib.ties())


def test_proposal_reads_as_a_hand_entry_sheet(week):
    ws, _ = week
    text = (ws / SYNC / "PROPOSAL.md").read_text()
    assert "| J1 | 2026-10-08 | Journal entry |" in text and "| C1 | 2026-10-09 | Credit card credit |" in text
    assert "**S1 - Expense (paid from store credit)**" in text
    assert "| Job Materials (80) |  | 37.10 | 123 Example St (201) |" in text   # J1 credit line, named via "labels"                               # J1 credit line: account, debit, credit, job


def test_check_config_catches_placeholders_and_bad_aliases(tmp_path):
    t = json.loads((REPO / "templates" / "hd-sync.example.json").read_text())
    (tmp_path / "a.json").write_text(json.dumps(t))
    problems = hdlib.check_config(str(tmp_path / "a.json"))
    assert any("<ID>" in p for p in problems) and any("YYYY-MM-DD" in p for p in problems)
    assert hdlib.check_config(str(SAMPLES / "sample_hd-sync.json")) == []
    bad = dict(CONFIG, po_aliases={"SHOP": "789 Nowhere Rd"}, cards={"11": "41"})
    (tmp_path / "b.json").write_text(json.dumps(bad))
    problems = hdlib.check_config(str(tmp_path / "b.json"))
    assert any("not in \"jobs\"" in p for p in problems) and any("4-digit" in p for p in problems)
