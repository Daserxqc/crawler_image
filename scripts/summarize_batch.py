import json
from tax_platform.store import list_persons

d = json.load(open("output/province_batch_progress.json", encoding="utf-8"))
print("=== first pass summary ===")
ok_l = ok_e = 0
for code, v in d["done"].items():
    leaders = v.get("leaders") or 0
    events = v.get("events") or 0
    if leaders or events:
        print(f"  {code}: leaders={leaders} events={events}")
        if leaders:
            ok_l += 1
        if events:
            ok_e += 1
print(f"provinces with leaders: {ok_l}, with events: {ok_e}, total done: {len(d['done'])}")
print("persons in db", len(list_persons()))
