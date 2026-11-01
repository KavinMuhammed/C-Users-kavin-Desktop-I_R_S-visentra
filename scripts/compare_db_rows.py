"""Compare every row that exists in a backup DB with the same row in the live DB (read-only on both).

    python scripts/compare_db_rows.py <backup.db> <live.db> [--out file.json]

Reports, per table, rows that were changed or removed since the backup and how many rows were added. Rows are matched
by primary key; tables without a single-column primary key are compared by full-row multiset.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter


def rows(d, t):
    pk = [r[1] for r in d.execute(f'pragma table_info("{t}")') if r[5] == 1]
    cols = [r[1] for r in d.execute(f'pragma table_info("{t}")')]
    data = d.execute(f'select * from "{t}"').fetchall()
    if len(pk) == 1:
        i = cols.index(pk[0])
        return {r[i]: r for r in data}, True
    return Counter(data), False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("backup"); ap.add_argument("live"); ap.add_argument("--out")
    a = ap.parse_args()
    b = sqlite3.connect(f"file:{a.backup}?mode=ro", uri=True); l = sqlite3.connect(f"file:{a.live}?mode=ro", uri=True)
    out = {"backup": a.backup, "live": a.live, "tables": {}, "pre_existing_rows_changed_or_removed": 0}
    for (t,) in b.execute("select name from sqlite_master where type='table' order by name"):
        rb, keyed = rows(b, t)
        rl, _ = rows(l, t)
        if keyed:
            changed = [k for k in rb if k in rl and rb[k] != rl[k]]
            removed = [k for k in rb if k not in rl]
            added = len([k for k in rl if k not in rb])
        else:
            removed = list((rb - rl).elements()); changed = []; added = sum((rl - rb).values())
        out["tables"][t] = {"backup_rows": sum(rb.values()) if not keyed else len(rb), "changed": len(changed), "removed": len(removed), "added": added,
                            "changed_keys": [str(k) for k in changed[:20]]}
        out["pre_existing_rows_changed_or_removed"] += len(changed) + len(removed)
    out["tables"] = {k: v for k, v in out["tables"].items() if v["changed"] or v["removed"] or v["added"]}
    txt = json.dumps(out, indent=1, default=str)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(txt)
    print(txt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
