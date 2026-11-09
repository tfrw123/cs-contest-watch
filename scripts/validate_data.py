import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
data = json.loads((root / "data/contests.json").read_text(encoding="utf-8"))
assert len(data["contests"]) == 6
assert {c["key"] for c in data["contests"]} == {"USACO", "ACSL", "BCC", "CCC", "AIO", "NZIC"}
for contest in data["contests"]:
    for event in contest.get("events", []):
        assert event.get("source", "").startswith("https://")
print("OK: six contests; event records include official source URLs")
