#!/usr/bin/env python3
"""
graph.py - a compact, append-only knowledge-graph engine for investment research.

Source of truth is four JSONL files inside a graph directory (default: ./kg):
  nodes.jsonl     identity records (companies, sovereigns, sectors, ...)
  edges.jsonl     every typed relation AND every time-stamped quantitative fact
  ontology.jsonl  the emergent type registry (predicates + node types)
  sources.jsonl   provenance registry (one row per source document)

Everything is stdlib-only. JSONL is grep/jq-able and line-appendable; this engine
adds identity-resolution, graph traversal, and a linter on top.

Usage:
  graph.py init [--dir kg]
  graph.py add-node   --id AAPL --type company --name "Apple Inc." [--aka iPhone] [--attr ticker=AAPL]
  graph.py add-pred   --p supplies_to --kind relation --domain company --range company \
                      --inverse supplied_by --card many
  graph.py add-src    --id 10k-aapl-2025 --title "Apple FY25 10-K" --url ... --kind filing --accessed 2026-06-16
  graph.py add-edge   --s AAPL --p supplies_to --o TSMC --as-of 2025-12-31 --src 10k-aapl-2025 [--conf 0.9]
  graph.py add-edge   --s AAPL --p revenue --val 391000 --unit USD_m --as-of 2025-09-28 --src 10k-aapl-2025
  graph.py find       "apple"
  graph.py neighbors  --id AAPL [--depth 1] [--pred supplies_to] [--dir out|in|both]
  graph.py path       --from AAPL --to TWN [--max-hops 4]
  graph.py subgraph   --id AAPL [--depth 2]
  graph.py stats
  graph.py validate
"""
import argparse, json, os, sys, difflib
from collections import defaultdict, deque

DIR = "kg"
FILES = {
    "nodes": "nodes.jsonl",
    "edges": "edges.jsonl",
    "ontology": "ontology.jsonl",
    "sources": "sources.jsonl",
}


# ---------------------------------------------------------------- io helpers
def path(name):
    return os.path.join(DIR, FILES[name])


def read(name):
    p = path(name)
    if not os.path.exists(p):
        return []
    out = []
    with open(p, encoding="utf-8") as f:
        for ln, raw in enumerate(f, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                out.append(json.loads(raw))
            except json.JSONDecodeError as e:
                print(f"WARN {FILES[name]}:{ln} bad JSON: {e}", file=sys.stderr)
    return out


def append(name, rec):
    os.makedirs(DIR, exist_ok=True)
    with open(path(name), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")


def index(name, key="id"):
    return {r[key]: r for r in read(name) if key in r}


# ---------------------------------------------------------------- commands
def cmd_init(a):
    os.makedirs(DIR, exist_ok=True)
    for name in FILES:
        p = path(name)
        if not os.path.exists(p):
            open(p, "a").close()
    idx = os.path.join(DIR, "index.md")
    if not os.path.exists(idx):
        with open(idx, "w", encoding="utf-8") as f:
            f.write("# Knowledge graph index\n\n"
                    "Derived view - regenerate from JSONL; never edit by hand.\n")
    print(f"initialized graph at {DIR}/")


def cmd_add_node(a):
    nodes = index("nodes")
    rec = {"id": a.id, "type": a.type, "name": a.name}
    if a.aka:
        rec["aka"] = a.aka
    for kv in a.attr or []:
        k, _, v = kv.partition("=")
        rec[k] = v
    if a.id in nodes:
        print(f"note: node {a.id} already exists; appending updated record (last wins)")
    append("nodes", rec)
    print(f"+node {a.id} ({a.type})")


def cmd_add_pred(a):
    rec = {"p": a.p, "kind": a.kind}
    for k in ("domain", "range", "inverse", "card", "unit", "note"):
        v = getattr(a, k, None)
        if v:
            rec[k] = v
    append("ontology", rec)
    print(f"+predicate {a.p} ({a.kind})")


def cmd_add_src(a):
    rec = {"id": a.id, "title": a.title}
    for k in ("url", "kind", "accessed", "note"):
        v = getattr(a, k, None)
        if v:
            rec[k] = v
    append("sources", rec)
    print(f"+source {a.id}")


def cmd_add_edge(a):
    rec = {"s": a.s, "p": a.p, "as_of": getattr(a, "as_of"), "src": a.src}
    if a.o is not None:
        rec["o"] = a.o
    elif a.val is not None:
        rec["val"] = _num(a.val)
        if a.unit:
            rec["unit"] = a.unit
    else:
        sys.exit("error: edge needs either --o (node ref) or --val (literal)")
    if a.conf is not None:
        rec["conf"] = float(a.conf)
    append("edges", rec)
    obj = rec.get("o", f"{rec.get('val')}{(' ' + rec['unit']) if 'unit' in rec else ''}")
    print(f"+edge {a.s} --{a.p}--> {obj}  @{rec['as_of']}")


def _num(s):
    try:
        return int(s)
    except ValueError:
        try:
            return float(s)
        except ValueError:
            return s


def cmd_find(a):
    q = a.query.lower()
    for n in read("nodes"):
        hay = " ".join([n.get("id", ""), n.get("name", ""), n.get("type", "")]
                       + n.get("aka", [])).lower()
        if q in hay:
            print(f"{n['id']:<16} {n.get('type',''):<12} {n.get('name','')}")


def _adj(pred=None, direction="both"):
    """build adjacency: id -> list of (neighbor_id, predicate, edge)"""
    adj = defaultdict(list)
    for e in read("edges"):
        if "o" not in e:  # literal metric, not a relation
            continue
        if pred and e["p"] != pred:
            continue
        if direction in ("out", "both"):
            adj[e["s"]].append((e["o"], e["p"], e))
        if direction in ("in", "both"):
            adj[e["o"]].append((e["s"], e["p"], e))
    return adj


def cmd_neighbors(a):
    nodes = index("nodes")
    adj = _adj(a.pred, a.direction)
    seen = {a.id}
    frontier = [(a.id, 0)]
    while frontier:
        cur, d = frontier.pop(0)
        if d >= a.depth:
            continue
        for nb, p, e in adj.get(cur, []):
            tag = nodes.get(nb, {}).get("name", nb)
            asof = e.get("as_of", "?")
            print(f"{'  '*d}{cur} --{p}--> {nb} ({tag}) @{asof}")
            if nb not in seen:
                seen.add(nb)
                frontier.append((nb, d + 1))


def cmd_path(a):
    adj = _adj(None, "both")
    start, goal = getattr(a, "from"), a.to
    q = deque([[start]])
    seen = {start}
    while q:
        p = q.popleft()
        if len(p) - 1 > a.max_hops:
            continue
        cur = p[-1]
        if cur == goal:
            print(" -> ".join(p))
            return
        for nb, _, _ in adj.get(cur, []):
            if nb not in seen:
                seen.add(nb)
                q.append(p + [nb])
    print(f"no path from {start} to {goal} within {a.max_hops} hops")


def cmd_subgraph(a):
    nodes = index("nodes")
    adj = _adj(None, "both")
    seen = {a.id}
    frontier = [(a.id, 0)]
    edges_out = []
    while frontier:
        cur, d = frontier.pop(0)
        if d >= a.depth:
            continue
        for nb, p, e in adj.get(cur, []):
            edges_out.append(e)
            if nb not in seen:
                seen.add(nb)
                frontier.append((nb, d + 1))
    print(json.dumps({
        "nodes": [nodes.get(i, {"id": i}) for i in sorted(seen)],
        "edges": edges_out,
    }, indent=2, ensure_ascii=False))


def cmd_stats(a):
    nodes, edges = read("nodes"), read("edges")
    onto, srcs = read("ontology"), read("sources")
    by_type = defaultdict(int)
    for n in nodes:
        by_type[n.get("type", "?")] += 1
    by_pred = defaultdict(int)
    rels = mets = 0
    for e in edges:
        by_pred[e.get("p", "?")] += 1
        if "o" in e:
            rels += 1
        else:
            mets += 1
    print(f"nodes={len(nodes)}  edges={len(edges)} (relations={rels} metrics={mets})  "
          f"predicates={len(onto)}  sources={len(srcs)}")
    print("node types:", dict(sorted(by_type.items(), key=lambda x: -x[1])))
    print("top predicates:",
          dict(sorted(by_pred.items(), key=lambda x: -x[1])[:10]))


# ---------------------------------------------------------------- linter
def cmd_validate(a):
    nodes = index("nodes")
    srcs = index("sources")
    onto = {o["p"]: o for o in read("ontology") if "p" in o}
    known_preds = list(onto.keys())
    edges = read("edges")
    errs, warns = [], []

    # contradiction tracking: (subject, functional-predicate, as_of) -> set(objects)
    functional = defaultdict(set)

    for i, e in enumerate(edges, 1):
        loc = f"edges.jsonl:{i}"
        # 1. structure
        if "s" not in e or "p" not in e:
            errs.append(f"{loc}: missing s/p")
            continue
        if "o" not in e and "val" not in e:
            errs.append(f"{loc}: edge has neither object (o) nor literal (val)")
        # 2. provenance is mandatory
        if not e.get("as_of"):
            errs.append(f"{loc}: missing as_of")
        if not e.get("src"):
            errs.append(f"{loc}: missing src")
        elif e["src"] not in srcs:
            errs.append(f"{loc}: src '{e['src']}' not in sources.jsonl")
        # 3. dangling node refs
        if e["s"] not in nodes:
            errs.append(f"{loc}: subject '{e['s']}' not a known node")
        if "o" in e and e["o"] not in nodes:
            errs.append(f"{loc}: object '{e['o']}' not a known node")
        # 4. predicate must be registered; else fuzzy-suggest to block synonym sprawl
        p = e["p"]
        if p not in onto:
            hint = difflib.get_close_matches(p, known_preds, n=1, cutoff=0.6)
            tip = f"  did you mean '{hint[0]}'?" if hint else ""
            errs.append(f"{loc}: predicate '{p}' not in ontology.jsonl{tip}")
        # 5. accumulate functional predicates for contradiction check
        elif onto[p].get("card") == "one" and "o" in e:
            functional[(e["s"], p, e.get("as_of"))].add(e["o"])

    # 6. contradictions: a functional (card=one) predicate with >1 object at same as_of
    for (s, p, asof), objs in functional.items():
        if len(objs) > 1:
            errs.append(f"contradiction: {s} --{p}--> {sorted(objs)} all @{asof} "
                        f"(predicate is card=one)")

    # orphan sources are only warnings
    used_src = {e.get("src") for e in edges}
    for sid in srcs:
        if sid not in used_src:
            warns.append(f"source '{sid}' is registered but unused")

    for w in warns:
        print(f"WARN  {w}")
    for er in errs:
        print(f"ERROR {er}")
    print(f"\n{len(errs)} error(s), {len(warns)} warning(s) over "
          f"{len(edges)} edges, {len(nodes)} nodes.")
    sys.exit(1 if errs else 0)


# ---------------------------------------------------------------- cli
def main():
    global DIR
    ap = argparse.ArgumentParser(description="compact investment knowledge-graph engine")
    ap.add_argument("--dir", default=DIR, help="graph directory (default: kg)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init")

    p = sub.add_parser("add-node")
    p.add_argument("--id", required=True); p.add_argument("--type", required=True)
    p.add_argument("--name", required=True); p.add_argument("--aka", action="append")
    p.add_argument("--attr", action="append", help="key=value, repeatable")

    p = sub.add_parser("add-pred")
    p.add_argument("--p", required=True)
    p.add_argument("--kind", required=True, choices=["relation", "metric"])
    p.add_argument("--domain"); p.add_argument("--range"); p.add_argument("--inverse")
    p.add_argument("--card", choices=["one", "many"]); p.add_argument("--unit")
    p.add_argument("--note")

    p = sub.add_parser("add-src")
    p.add_argument("--id", required=True); p.add_argument("--title", required=True)
    p.add_argument("--url"); p.add_argument("--kind"); p.add_argument("--accessed")
    p.add_argument("--note")

    p = sub.add_parser("add-edge")
    p.add_argument("--s", required=True); p.add_argument("--p", required=True)
    p.add_argument("--o"); p.add_argument("--val"); p.add_argument("--unit")
    p.add_argument("--as-of", dest="as_of", required=True); p.add_argument("--src", required=True)
    p.add_argument("--conf")

    p = sub.add_parser("find"); p.add_argument("query")

    p = sub.add_parser("neighbors")
    p.add_argument("--id", required=True); p.add_argument("--depth", type=int, default=1)
    p.add_argument("--pred")
    p.add_argument("--dir", dest="direction", choices=["out", "in", "both"], default="both")

    p = sub.add_parser("path")
    p.add_argument("--from", required=True); p.add_argument("--to", required=True)
    p.add_argument("--max-hops", dest="max_hops", type=int, default=4)

    p = sub.add_parser("subgraph")
    p.add_argument("--id", required=True); p.add_argument("--depth", type=int, default=2)

    sub.add_parser("stats")
    sub.add_parser("validate")

    a = ap.parse_args()
    DIR = a.dir
    {
        "init": cmd_init, "add-node": cmd_add_node, "add-pred": cmd_add_pred,
        "add-src": cmd_add_src, "add-edge": cmd_add_edge, "find": cmd_find,
        "neighbors": cmd_neighbors, "path": cmd_path, "subgraph": cmd_subgraph,
        "stats": cmd_stats, "validate": cmd_validate,
    }[a.cmd](a)


if __name__ == "__main__":
    main()
