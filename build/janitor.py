"""Monthly janitor for the originals archive.
Reads discovery/statuses.json (written by the sheet). KILLED posts older than 60 days lose their originals;
everything else is kept. Prints a short report. Never touches POSTED or DRAFT posts."""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import archive  # noqa: E402

path = os.path.join(ROOT, "discovery", "statuses.json")
if not os.path.exists(path):
    print("no statuses.json yet; nothing to do")
    sys.exit(0)
rows = json.load(open(path)).get("rows", {})
now = time.time()
removed = 0
for idx, r in rows.items():
    if r.get("status") != "KILLED":
        continue
    try:
        age = (now - time.mktime(time.strptime(r.get("updated", "")[:10], "%Y-%m-%d"))) / 86400
    except Exception:  # noqa: BLE001
        age = 0
    if age >= 60:
        print(idx, archive.delete(idx))
        removed += 1
print(f"janitor: {removed} killed posts cleared; {sum(1 for r in rows.values() if r.get('status') == 'POSTED')} posted posts kept")
