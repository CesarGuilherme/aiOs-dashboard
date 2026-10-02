"""Build a tmp Second Brain (nodes/ + brain.db) with the schema memory_parsing reads.

The DDL mirrors ~/.brain/scripts/brain.py — the columns the dashboard relies on
are the contract documented in token_dashboard/memory_parsing.py.
"""
import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE nodes(
  id TEXT PRIMARY KEY, parent_id TEXT, kind TEXT, type TEXT, name TEXT,
  summary TEXT, body TEXT, file TEXT, workspaces TEXT, status TEXT, source TEXT,
  date TEXT, mtime REAL, depth INTEGER, size INTEGER DEFAULT 0);
CREATE TABLE edges(src TEXT, dst TEXT, kind TEXT, why TEXT);
"""


def make_brain(root, nodes, edges=()):
    """nodes: dicts with at least id + kind; returns the nodes/ dir path (str).

    Memory nodes get a real .md file under nodes/ so mutation tests can move it.
    """
    root = Path(root)
    nodes_dir = root / "nodes"
    nodes_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(root / "brain.db")
    con.executescript(SCHEMA)
    for n in nodes:
        nid = n["id"]
        row = {
            "id": nid, "parent_id": n.get("parent_id", nid.rsplit("/", 1)[0] if "/" in nid else None),
            "kind": n["kind"], "type": n.get("type", ""), "name": n.get("name", nid.split("/")[-1]),
            "summary": n.get("summary", ""), "body": n.get("body", ""), "file": "",
            "workspaces": json.dumps(n.get("workspaces", [])), "status": "",
            "source": n.get("source", ""), "date": n.get("date", ""),
            "mtime": n.get("mtime", time.time()), "depth": nid.count("/") + 1, "size": n.get("size", 0),
        }
        if n["kind"] == "memory" and n.get("type") != "learning":
            f = nodes_dir / f"{nid}.md"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(n.get("text", f"---\nname: {row['name']}\n"
                                       f"source: {row['source'] or 'user'}\n---\n{row['body']}\n"))
            row["file"] = str(f)
        con.execute(f"INSERT INTO nodes({','.join(row)}) VALUES({','.join('?' * len(row))})",
                    list(row.values()))
    con.executemany("INSERT INTO edges VALUES(?,?,?,?)", list(edges))
    con.commit()
    con.close()
    return str(nodes_dir)
