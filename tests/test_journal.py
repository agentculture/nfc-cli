import json

from nfc_cli.nfc.journal import Journal


def test_append_reload_and_pending(tmp_path):
    p = tmp_path / "sub" / "j.jsonl"
    j = Journal(p)
    j.append(4, "00" * 16, "11" * 16)
    j.append(5, "00" * 16, "22" * 16, written=True, verified=True)
    j.append(4, "00" * 16, "11" * 16, written=True)
    j2 = Journal(p)
    assert j2.records == j.records
    assert j2.pending() == [4]
    j2.append(4, "00" * 16, "11" * 16, written=True, verified=True)
    assert Journal(p).pending() == []


def test_lines_are_json_and_torn_tail_ignored(tmp_path):
    p = tmp_path / "j.jsonl"
    j = Journal(p)
    j.append(1, "a", "b")
    with open(p, "a") as fh:
        fh.write('{"block": 2, "ol')
    assert [r["block"] for r in Journal(p).records] == [1]
    assert json.loads(p.read_text().splitlines()[0])["new"] == "b"
