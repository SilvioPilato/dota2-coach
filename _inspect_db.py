import sqlite3
import json
from pathlib import Path

db = Path.home() / ".dota_coach" / "history.db"
conn = sqlite3.connect(str(db))

tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print("Tables:", [t[0] for t in tables])

for (t,) in tables:
    print(f"\n-- {t}")
    cur = conn.execute(f"SELECT * FROM {t} LIMIT 0")
    cols = [d[0] for d in cur.description]
    print("Cols:", cols)
    rows = conn.execute(f"SELECT * FROM {t} ORDER BY rowid DESC LIMIT 5").fetchall()
    for r in rows:
        row_dict = dict(zip(cols, r))
        # Truncate large fields
        for k, v in row_dict.items():
            if isinstance(v, str) and len(v) > 200:
                row_dict[k] = v[:200] + "..."
        print(row_dict)

conn.close()
