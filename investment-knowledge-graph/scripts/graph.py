#!/usr/bin/env python3
"""
graph.py - a compact, append-only knowledge-graph engine for investment research.

Source of truth is four JSONL files inside a graph directory (default: ./kg):
  nodes.jsonl     identity records (companies, sovereigns, sectors, doc pointers, ...)
  edges.jsonl     every typed relation AND every time-stamped quantitative fact
  ontology.jsonl  the emergent type registry (predicates + node types)
  sources.jsonl   provenance registry (one row per source document)

A fifth, OPTIONAL layer is a conformant OKF bundle under <dir>/docs/ - markdown
concept files (entity dossiers, attached excerpts/notes, and archived source text
under docs/references/). The graph stays lightweight for traversal; OKF files hold
the heavy content you open on demand. A node of kind "okf" is just a *pointer* to a
file, so one entity can reference many docs and one doc can be shared across entities.

Conformance contract (enforced by `validate`):
  1. every node has id + type
  2. every edge has s, p, as_of, src, and exactly one of o (relation) or val (metric)
  3. every src resolves to a sources.jsonl entry
  4. every predicate p is registered in ontology.jsonl
  5. no functional (card=one) predicate has conflicting objects at the same as_of
  6. every doc pointer / archived source resolves to a file on disk

Everything is stdlib-only. JSONL is grep/jq-able and line-appendable.

Usage:
  graph.py init [--dir kg]
  graph.py add-node    --id AAPL --type company --name "Apple Inc." [--aka iPhone] [--attr ticker=AAPL]
  graph.py add-pred    --p supplies_to --kind relation --domain company --range company --inverse supplied_by --card many
  graph.py add-src     --id 10k-aapl-2025 --title "Apple FY25 10-K" --url ... --kind filing --accessed 2026-06-16
  graph.py add-edge    --s AAPL --p supplies_to --o TSMC --as-of 2025-12-31 --src 10k-aapl-2025 [--conf 0.9]
  graph.py add-edge    --s AAPL --p revenue --val 391000 --unit USD_m --as-of 2025-09-28 --src 10k-aapl-2025
  graph.py attach-doc  --id excerpts/tsmc-cap --type excerpt --title "..." --for AAPL --for TSMC --rel has_excerpt --src 10k-aapl-2025 --body "..."
  graph.py archive-src --id 10k-aapl-2025 --title "Apple FY25 10-K" --url ... --kind filing --body-file extract.txt
  graph.py export-okf  [--out kg/docs]
  graph.py read-doc    --id excerpts/tsmc-cap
  graph.py find        "apple"
  graph.py neighbors   --id AAPL [--depth 1] [--pred supplies_to] [--dir out|in|both]
  graph.py path        --from AAPL --to TWN [--max-hops 4]
  graph.py subgraph    --id AAPL [--depth 2]
  graph.py stats
  graph.py validate
"""
import argparse, json, os, sys, difflib, datetime
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


def _today():
    return datetime.date.today().isoformat()


def _is_doc(n):
    """A doc node is a lightweight pointer to an OKF content file."""
    return n.get("kind") == "okf" or "path" in n


def _safe(s):
    return s.replace("/", "_").replace(os.sep, "_")


def _yaml(v):
    if isinstance(v, list):
        return "[" + ", ".join(str(x) for x in v) + "]"
    return str(v)


def _stamp(d):
    """ISO date -> ISO datetime (OKF timestamp prefers full datetime)."""
    return d + "T00:00:00Z" if d and len(d) == 10 else (d or "")


def _titlecase(t):
    """node-type slug -> OKF-style display type: company -> Company, info_tech -> Info Tech."""
    return (t or "").replace("_", " ").replace("-", " ").strip().title() or t


# ---------------------------------------------------------------- commands
def cmd_init(a):
    os.makedirs(DIR, exist_ok=True)
    for name in FILES:
        p = path(name)
        if not os.path.exists(p):
            open(p, "a").close()
    os.makedirs(os.path.join(DIR, "docs"), exist_ok=True)
    idx = os.path.join(DIR, "index.md")
    if not os.path.exists(idx):
        with open(idx, "w", encoding="utf-8") as f:
            f.write("# Knowledge graph index\n\n"
                    "Derived view - regenerate from JSONL; never edit by hand.\n"
                    "Human-readable OKF bundle lives under docs/.\n")
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


# ---------------------------------------------------------------- OKF content layer
def cmd_attach_doc(a):
    """Write an OKF concept file, register it as a lightweight doc node, and
    link it from one or more entities. The same doc can back many entities."""
    docid = a.id.strip("/")
    title = a.title
    rel = a.rel
    as_of = a.as_of or _today()
    bundle = os.path.join(DIR, "docs")
    fpath = os.path.join(bundle, *docid.split("/")) + ".md"
    os.makedirs(os.path.dirname(fpath), exist_ok=True)

    body = ""
    if a.body_file:
        with open(a.body_file, encoding="utf-8") as f:
            body = f.read().rstrip() + "\n"
    elif a.body:
        body = a.body.rstrip() + "\n"

    # register a source so the reference edges have valid provenance
    srcs = index("sources")
    doc_src_id = a.src or docid
    if doc_src_id not in srcs:
        srow = {"id": doc_src_id, "title": title, "kind": a.type, "accessed": _today()}
        if a.url:
            srow["url"] = a.url
        append("sources", srow)

    # frontmatter (OKF: type is the only required field)
    fm = {"type": a.type, "title": title}
    if a.desc:
        fm["description"] = a.desc
    if a.url:
        fm["resource"] = a.url
    if a.tags:
        fm["tags"] = a.tags
    fm["timestamp"] = _stamp(as_of)
    if a.for_:
        fm["node_refs"] = a.for_
    fm["src"] = doc_src_id

    with open(fpath, "w", encoding="utf-8") as f:
        f.write("---\n")
        for k, v in fm.items():
            f.write(f"{k}: {_yaml(v)}\n")
        f.write("---\n\n")
        if body:
            f.write(body if body.lstrip().startswith("#") else f"# {a.type.title()}\n\n{body}")
        else:
            f.write(f"# {title}\n\n_(content to be filled in)_\n")
        if a.url:
            f.write(f"\n# Citations\n\n[1] [{title}]({a.url})\n")

    # doc node = pointer only (no heavy content in the graph)
    relpath = os.path.relpath(fpath, DIR).replace(os.sep, "/")
    append("nodes", {"id": docid, "type": a.type, "name": title,
                     "path": relpath, "kind": "okf"})

    # auto-register the reference predicate so the linter stays clean
    onto = {o["p"]: o for o in read("ontology") if "p" in o}
    if rel not in onto:
        append("ontology", {"p": rel, "kind": "relation", "range": "doc",
                            "inverse": "documents", "card": "many",
                            "note": "reference to an OKF content document"})
        print(f"+predicate {rel} (relation, auto-registered for doc references)")

    for ent in a.for_ or []:
        append("edges", {"s": ent, "p": rel, "o": docid,
                         "as_of": as_of, "src": doc_src_id})
        print(f"+edge {ent} --{rel}--> {docid}  @{as_of}")
    print(f"+doc {docid} -> {relpath}")


def cmd_archive_src(a):
    """Archive the salient extracted text of a source as a first-class OKF concept
    under docs/references/, and record the archive path in sources.jsonl. Gives
    provenance real durability when the original url rots or is paywalled."""
    srcs = index("sources")
    existing = srcs.get(a.id, {})
    title = a.title or existing.get("title", a.id)
    kind = a.kind or existing.get("kind", "reference")
    url = a.url or existing.get("url")
    accessed = a.accessed or a.as_of or existing.get("accessed") or _today()

    safe = _safe(a.id)
    relpath = f"docs/references/{safe}.md"
    fpath = os.path.join(DIR, "docs", "references", safe + ".md")
    os.makedirs(os.path.dirname(fpath), exist_ok=True)

    body = ""
    if a.body_file:
        with open(a.body_file, encoding="utf-8") as f:
            body = f.read().rstrip() + "\n"
    elif a.body:
        body = a.body.rstrip() + "\n"

    fm = {"type": _titlecase(kind), "title": title}
    if a.desc:
        fm["description"] = a.desc
    fm["resource"] = url or f"src://{a.id}"
    fm["kind"] = kind
    fm["src_id"] = a.id
    fm["timestamp"] = _stamp(accessed)

    with open(fpath, "w", encoding="utf-8") as f:
        f.write("---\n")
        for k, v in fm.items():
            f.write(f"{k}: {_yaml(v)}\n")
        f.write("---\n\n")
        if body:
            f.write(body if body.lstrip().startswith("#") else f"# {title}\n\n{body}")
        else:
            f.write(f"# {title}\n\n_(archived source text to be filled in)_\n")
        if url:
            f.write(f"\n# Citations\n\n[1] [{title}]({url})\n")

    # update the registry (append a merged row; index() is last-wins)
    row = dict(existing)
    row["id"] = a.id
    row["title"] = title
    row["kind"] = kind
    if url:
        row["url"] = url
    row["accessed"] = accessed
    row["archive"] = relpath
    append("sources", row)
    print(f"+archived source {a.id} -> {relpath}")


def cmd_read_doc(a):
    """Progressive disclosure: open one OKF file only when needed."""
    nodes = index("nodes")
    cands = []
    if a.id in nodes and "path" in nodes[a.id]:
        cands.append(os.path.join(DIR, nodes[a.id]["path"]))
    cands.append(os.path.join(DIR, "docs", *a.id.split("/")) + ".md")
    cands.append(os.path.join(DIR, "docs", "references", _safe(a.id) + ".md"))
    for p in cands:
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                sys.stdout.write(f.read())
            return
    sys.exit(f"no document found for '{a.id}'")


def cmd_export_okf(a):
    """Generate a conformant OKF bundle (entity dossiers + indexes + log) from the
    graph. Attached docs and archived sources are left untouched; only dossiers and
    indexes are regenerated."""
    out = a.out or os.path.join(DIR, "docs")
    canonical = os.path.abspath(out) == os.path.abspath(os.path.join(DIR, "docs"))
    nodes = index("nodes")
    edges = read("edges")
    srcs = index("sources")
    os.makedirs(out, exist_ok=True)

    docnodes = {i: n for i, n in nodes.items() if _is_doc(n)}
    entities = {i: n for i, n in nodes.items() if not _is_doc(n)}

    # id -> bundle-relative link (OKF recommends absolute "/..." form)
    link = {}
    for i, n in entities.items():
        link[i] = f"/{_safe(n.get('type', 'entity'))}/{_safe(i)}.md"
    for i, n in docnodes.items():
        p = n.get("path", f"docs/{i}.md")
        link[i] = "/" + (p[5:] if p.startswith("docs/") else p)

    def src_link(sid):
        s = srcs.get(sid, {})
        arch = s.get("archive")
        if arch:
            return "/" + (arch[5:] if arch.startswith("docs/") else arch)
        return None

    out_e, in_e = defaultdict(list), defaultdict(list)
    for e in edges:
        out_e[e["s"]].append(e)
        if "o" in e:
            in_e[e["o"]].append(e)

    written = 0
    by_type = defaultdict(list)
    for i, n in entities.items():
        typ = n.get("type", "entity")
        by_type[typ].append(i)
        rels, docs, mets, used_src = defaultdict(list), [], [], []
        for e in out_e.get(i, []):
            if e.get("src"):
                used_src.append(e["src"])
            if "val" in e:
                mets.append(e)
            elif e.get("o") in docnodes:
                docs.append(e)
            elif "o" in e:
                rels[e["p"]].append(e)
        incoming = [e for e in in_e.get(i, []) if e["s"] in entities]

        asofs = [e.get("as_of") for e in out_e.get(i, []) if e.get("as_of")]
        ts = _stamp(max(asofs) if asofs else _today())

        # OKF-conformant frontmatter: Title-cased type + resource back-pointer to node
        fm = {"type": _titlecase(typ), "title": n.get("name", i)}
        if n.get("description"):
            fm["description"] = n["description"]
        if n.get("tags"):
            fm["tags"] = n["tags"]
        fm["timestamp"] = ts
        fm["resource"] = f"kg://node/{i}"
        fm["node_id"] = i
        fm["node_type"] = typ

        L = ["---"]
        for k, v in fm.items():
            L.append(f"{k}: {_yaml(v)}")
        L.append("---\n")

        skip = {"id", "type", "name", "aka", "kind", "path",
                "description", "tags", "url", "resource"}
        attrs = {k: v for k, v in n.items() if k not in skip}
        if n.get("aka") or attrs:
            L.append("# Overview\n")
            if n.get("aka"):
                L.append(f"Also known as: {', '.join(n['aka'])}.\n")
            for k, v in attrs.items():
                L.append(f"- **{k}**: {v}")
            L.append("")

        if rels:
            L.append("# Relationships\n")
            for p in sorted(rels):
                L.append(f"## {p}\n")
                for e in rels[p]:
                    o = e["o"]
                    nm = nodes.get(o, {}).get("name", o)
                    tgt = link.get(o, f"/{_safe(o)}.md")
                    conf = f" (conf {e['conf']})" if "conf" in e else ""
                    L.append(f"- [{nm}]({tgt}) - @{e.get('as_of','?')}, "
                             f"src `{e.get('src','?')}`{conf}")
                L.append("")

        if mets:
            L.append("# Metrics\n")
            L.append("| metric | value | unit | as_of | src |")
            L.append("|--------|-------|------|-------|-----|")
            for e in mets:
                L.append(f"| {e['p']} | {e.get('val')} | {e.get('unit','')} "
                         f"| {e.get('as_of','')} | {e.get('src','')} |")
            L.append("")

        if docs:
            L.append("# Documents\n")
            for e in docs:
                o = e["o"]
                nm = nodes.get(o, {}).get("name", o)
                L.append(f"- [{nm}]({link.get(o)}) - {e['p']}, @{e.get('as_of','?')}")
            L.append("")

        if incoming:
            L.append("# Referenced by\n")
            for e in incoming:
                s = e["s"]
                nm = nodes.get(s, {}).get("name", s)
                L.append(f"- [{nm}]({link.get(s)}) - {e['p']} @{e.get('as_of','?')}")
            L.append("")

        uniq = []
        for sid in used_src:
            if sid not in uniq:
                uniq.append(sid)
        if uniq:
            L.append("# Citations\n")
            for idx, sid in enumerate(uniq, 1):
                s = srcs.get(sid, {})
                t, u = s.get("title", sid), s.get("url")
                bl = src_link(sid)
                if bl:
                    extra = f" - original: {u}" if u else ""
                    L.append(f"[{idx}] [{t}]({bl}){extra}")
                elif u:
                    L.append(f"[{idx}] [{t}]({u})")
                else:
                    L.append(f"[{idx}] {t} (`{sid}`)")
            L.append("")

        fpath = os.path.join(out, _safe(typ), _safe(i) + ".md")
        os.makedirs(os.path.dirname(fpath), exist_ok=True)
        with open(fpath, "w", encoding="utf-8") as f:
            f.write("\n".join(L).rstrip() + "\n")
        written += 1

    # per-type entity indexes
    idx_dirs = []
    for typ, ids in sorted(by_type.items()):
        d = _safe(typ)
        idx_dirs.append(d)
        os.makedirs(os.path.join(out, d), exist_ok=True)
        with open(os.path.join(out, d, "index.md"), "w", encoding="utf-8") as f:
            f.write(f"# {_titlecase(typ)}\n\n")
            for i in sorted(ids):
                nm = entities[i].get("name", i)
                desc = entities[i].get("description", "")
                f.write(f"* [{nm}]({_safe(i)}.md)" + (f" - {desc}" if desc else "") + "\n")

    # per-dir doc indexes (do not touch the doc files themselves)
    doc_by_dir = defaultdict(list)
    for i, n in docnodes.items():
        p = n.get("path", f"docs/{i}.md")
        rel = p[5:] if p.startswith("docs/") else p
        doc_by_dir[os.path.dirname(rel) or "."].append((i, n, rel))
    for d, items in sorted(doc_by_dir.items()):
        if d == ".":
            continue
        idx_dirs.append(d)
        os.makedirs(os.path.join(out, d), exist_ok=True)
        with open(os.path.join(out, d, "index.md"), "w", encoding="utf-8") as f:
            f.write(f"# {d}\n\n")
            for i, n, rel in sorted(items):
                f.write(f"* [{n.get('name', i)}]({os.path.basename(rel)}) - {n.get('type','')}\n")

    # archived-source index (references/) — only when writing the canonical bundle
    archived = [(sid, s) for sid, s in srcs.items() if s.get("archive")]
    if archived and canonical:
        idx_dirs.append("references")
        os.makedirs(os.path.join(out, "references"), exist_ok=True)
        with open(os.path.join(out, "references", "index.md"), "w", encoding="utf-8") as f:
            f.write("# references\n\nArchived source material (durable provenance).\n\n")
            for sid, s in sorted(archived):
                base = os.path.basename(s["archive"])
                f.write(f"* [{s.get('title', sid)}]({base}) - {s.get('kind','source')}\n")

    # root index (the only index.md allowed to carry frontmatter)
    with open(os.path.join(out, "index.md"), "w", encoding="utf-8") as f:
        f.write("---\nokf_version: \"0.1\"\n---\n\n")
        f.write("# Investment knowledge graph - OKF bundle\n\n")
        f.write("Derived view generated from the graph. Entity dossiers, attached "
                "documents, and archived source material. Traverse the JSONL graph for "
                "queries; open these files for detail.\n\n")
        for d in sorted(set(idx_dirs)):
            f.write(f"* [{d}/]({d}/index.md)\n")

    with open(os.path.join(out, "log.md"), "a", encoding="utf-8") as f:
        f.write(f"\n## {_today()}\n* **Export**: regenerated {written} entity "
                f"dossier(s); {len(docnodes)} attached doc(s); {len(archived)} archived source(s).\n")

    print(f"OKF bundle written to {out}/  ({written} dossiers, "
          f"{len(docnodes)} docs, {len(archived)} archived sources, "
          f"{len(set(idx_dirs))} sections)")


# ---------------------------------------------------------------- query / traverse
def cmd_find(a):
    q = a.query.lower()
    for n in read("nodes"):
        hay = " ".join([n.get("id", ""), n.get("name", ""), n.get("type", "")]
                       + n.get("aka", [])).lower()
        if q in hay:
            print(f"{n['id']:<22} {n.get('type',''):<12} {n.get('name','')}")


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
    nodes = list(index("nodes").values()); edges = read("edges")
    onto = read("ontology"); srcs = list(index("sources").values())
    by_type = defaultdict(int)
    docs = 0
    for n in nodes:
        by_type[n.get("type", "?")] += 1
        if _is_doc(n):
            docs += 1
    archived = sum(1 for s in srcs if s.get("archive"))
    by_pred = defaultdict(int)
    rels = mets = 0
    for e in edges:
        by_pred[e.get("p", "?")] += 1
        if "o" in e:
            rels += 1
        else:
            mets += 1
    print(f"nodes={len(nodes)} (docs={docs})  edges={len(edges)} "
          f"(relations={rels} metrics={mets})  predicates={len(onto)}  "
          f"sources={len(srcs)} (archived={archived})")
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

    functional = defaultdict(set)

    # contract rule 1: every node has id + type
    for i, n in nodes.items():
        if not n.get("type"):
            errs.append(f"nodes.jsonl: node '{i}' missing type")

    for i, e in enumerate(edges, 1):
        loc = f"edges.jsonl:{i}"
        if "s" not in e or "p" not in e:
            errs.append(f"{loc}: missing s/p")
            continue
        # contract rule 2: exactly one of o / val
        if "o" not in e and "val" not in e:
            errs.append(f"{loc}: edge has neither object (o) nor literal (val)")
        if "o" in e and "val" in e:
            errs.append(f"{loc}: edge has BOTH object (o) and literal (val); use one")
        if not e.get("as_of"):
            errs.append(f"{loc}: missing as_of")
        if not e.get("src"):
            errs.append(f"{loc}: missing src")
        elif e["src"] not in srcs:
            errs.append(f"{loc}: src '{e['src']}' not in sources.jsonl")
        if e["s"] not in nodes:
            errs.append(f"{loc}: subject '{e['s']}' not a known node")
        if "o" in e and e["o"] not in nodes:
            errs.append(f"{loc}: object '{e['o']}' not a known node")
        p = e["p"]
        if p not in onto:
            hint = difflib.get_close_matches(p, known_preds, n=1, cutoff=0.6)
            tip = f"  did you mean '{hint[0]}'?" if hint else ""
            errs.append(f"{loc}: predicate '{p}' not in ontology.jsonl{tip}")
        elif onto[p].get("card") == "one" and "o" in e:
            functional[(e["s"], p, e.get("as_of"))].add(e["o"])

    for (s, p, asof), objs in functional.items():
        if len(objs) > 1:
            errs.append(f"contradiction: {s} --{p}--> {sorted(objs)} all @{asof} "
                        f"(predicate is card=one)")

    # contract rule 6: doc pointers + archived sources resolve to a file
    for i, n in nodes.items():
        if _is_doc(n) and "path" in n and not os.path.exists(os.path.join(DIR, n["path"])):
            warns.append(f"doc node '{i}' points to missing file {n['path']}")
    for sid, s in srcs.items():
        if s.get("archive") and not os.path.exists(os.path.join(DIR, s["archive"])):
            warns.append(f"source '{sid}' archive file missing: {s['archive']}")

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

    p = sub.add_parser("attach-doc")
    p.add_argument("--id", required=True, help="OKF concept id, e.g. excerpts/tsmc-cap")
    p.add_argument("--title", required=True)
    p.add_argument("--type", default="excerpt", help="OKF type: excerpt, note, reference, ...")
    p.add_argument("--rel", default="has_excerpt", help="predicate linking entity -> doc")
    p.add_argument("--src", help="source id backing the link (defaults to the doc itself)")
    p.add_argument("--url")
    p.add_argument("--as-of", dest="as_of")
    p.add_argument("--desc")
    p.add_argument("--tags", action="append")
    p.add_argument("--for", dest="for_", action="append", help="entity id to attach to, repeatable")
    p.add_argument("--body"); p.add_argument("--body-file")

    p = sub.add_parser("archive-src")
    p.add_argument("--id", required=True, help="source id to archive")
    p.add_argument("--title"); p.add_argument("--url"); p.add_argument("--kind")
    p.add_argument("--accessed"); p.add_argument("--desc")
    p.add_argument("--as-of", dest="as_of")
    p.add_argument("--body"); p.add_argument("--body-file")

    p = sub.add_parser("export-okf")
    p.add_argument("--out", help="bundle dir (default: <dir>/docs)")

    p = sub.add_parser("read-doc")
    p.add_argument("--id", required=True)

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
        "add-src": cmd_add_src, "add-edge": cmd_add_edge,
        "attach-doc": cmd_attach_doc, "archive-src": cmd_archive_src,
        "export-okf": cmd_export_okf, "read-doc": cmd_read_doc,
        "find": cmd_find, "neighbors": cmd_neighbors, "path": cmd_path,
        "subgraph": cmd_subgraph, "stats": cmd_stats, "validate": cmd_validate,
    }[a.cmd](a)


if __name__ == "__main__":
    main()
