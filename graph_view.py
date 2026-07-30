"""Dump Tiwa's knowledge graph to an interactive HTML page.

Usage: py graph_view.py          -> writes data/graph.html, open it in a browser
       py graph_view.py --open   -> also opens it

NEEDS INTERNET to view: the page pulls vis-network from a CDN, so it renders blank
offline. The control panel's memory tab (dashboard.py) covers the same ground with
search and per-row delete and no network — use this only when you specifically want
the force-directed picture of who is connected to whom.
"""
import json
import sys
import webbrowser

from tiwa import memory

db = memory.connect()
nodes = [
    {"id": i, "label": n, "color": "#f4a5c0" if n == memory.TIWA else "#9fd8f5"}
    for i, n in db.execute("SELECT id, name FROM entities")
]
edges = [
    {"from": s, "to": d, "label": r, "title": note or r, "arrows": "to"}
    for s, r, d, note in db.execute("SELECT src, rel, dst, note FROM relations")
]

html = f"""<!doctype html><meta charset="utf-8"><title>Tiwa graph</title>
<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
<div id="g" style="height:96vh"></div>
<script>
new vis.Network(document.getElementById("g"),
  {{nodes: {json.dumps(nodes, ensure_ascii=False)},
    edges: {json.dumps(edges, ensure_ascii=False)}}},
  {{nodes: {{shape: "box", font: {{size: 16}}}},
    edges: {{font: {{size: 11, align: "middle"}}}}}});
</script>"""

out = memory.DATA_DIR / "graph.html"
out.write_text(html, encoding="utf-8")
print(f"wrote {out} — {len(nodes)} nodes, {len(edges)} edges")
if "--open" in sys.argv:
    webbrowser.open(out.as_uri())
