# Schema reference

Field specs for the four JSONL stores, then a worked example for each relation type
the research workflow cares about. All records are single-line JSON; shown pretty here
for readability.

## nodes.jsonl

| field | req | meaning |
|-------|-----|---------|
| `id`  | yes | short stable key you choose (ticker, ISO-3166 a3, sector slug) |
| `type`| yes | emergent node type: `company`, `country`, `sector`, `segment`, `product`, `person`, ... |
| `name`| yes | display name |
| `aka` | no  | array of synonyms used for identity resolution in `find` |
| *any* | no  | flat scalar attributes intrinsic to identity (`ticker`, `iso`, `gics`) |

Keep *facts* out of nodes — anything time-varying or sourced belongs in an edge.

## edges.jsonl

| field   | req | meaning |
|---------|-----|---------|
| `s`     | yes | subject node id |
| `p`     | yes | predicate (must exist in ontology.jsonl) |
| `o`     | rel | object node id — present for **relations** |
| `val`   | met | literal value — present for **metrics** (number or string) |
| `unit`  | no  | unit for a metric (`USD_m`, `pct`, `count`) |
| `as_of` | yes | ISO date the fact is true *for* |
| `src`   | yes | source id (must exist in sources.jsonl) |
| `conf`  | no  | 0–1 confidence; include for inferred/uncertain facts |

An edge is a relation **xor** a metric: it has `o`, or it has `val` — never both.

## ontology.jsonl

| field     | req | meaning |
|-----------|-----|---------|
| `p`       | yes | predicate name (canonical direction) |
| `kind`    | yes | `relation` or `metric` |
| `domain`  | no  | expected subject node type |
| `range`   | no  | expected object node type (relations) |
| `inverse` | no  | predicate name for walking the edge backwards |
| `card`    | no  | `one` (functional — triggers contradiction check) or `many` |
| `unit`    | no  | default unit (metrics) |
| `note`    | no  | free text definition to prevent synonym drift |

## sources.jsonl

| field      | req | meaning |
|------------|-----|---------|
| `id`       | yes | short source key referenced by `src` on edges |
| `title`    | yes | human title |
| `url`      | no  | link or file path |
| `kind`     | no  | `filing`, `transcript`, `news`, `analyst`, `dialogue`, ... |
| `accessed` | no  | ISO date the source was read |

When a fact comes from the live conversation itself, register a `dialogue` source so
provenance is never blank.

---

## Worked examples by relation type

### Country of risk (functional)
Where a company's principal risk is domiciled — one value per `as_of`.
```json
{"p":"country_of_risk","kind":"relation","domain":"company","range":"country","inverse":"risk_to","card":"one"}
{"s":"TSMC","p":"country_of_risk","o":"TWN","as_of":"2026-01-01","src":"analyst-geo-2026"}
```
Two different `o` at the same `as_of` is a contradiction the linter will flag; a new `o`
at a later `as_of` is a legitimate change over time.

### Sector / industry hierarchy (multi-level, recursive)
Model GICS-style levels with one self-referential predicate on `sector` nodes.
```json
{"p":"parent_sector","kind":"relation","domain":"sector","range":"sector","inverse":"sub_sector","card":"one"}
{"p":"in_sector","kind":"relation","domain":"company","range":"sector","inverse":"has_member","card":"many"}
{"s":"semiconductors","p":"parent_sector","o":"tech_hardware","as_of":"2026-01-01","src":"gics-2026"}
{"s":"tech_hardware","p":"parent_sector","o":"info_tech","as_of":"2026-01-01","src":"gics-2026"}
{"s":"TSMC","p":"in_sector","o":"semiconductors","as_of":"2026-01-01","src":"gics-2026"}
```
"All companies under Information Technology" = walk `sub_sector` down, then `has_member`.

### Supply-chain network
Directional dependency between firms; keep the canonical direction only.
```json
{"p":"supplies_to","kind":"relation","domain":"company","range":"company","inverse":"supplied_by","card":"many"}
{"s":"TSMC","p":"supplies_to","o":"AAPL","as_of":"2025-12-31","src":"10k-aapl-2025","conf":0.95}
```
Add `val`/`unit` as a *separate* metric edge if you know the dependency magnitude
(e.g. `share_of_cogs`).

### Segment / line-of-business technology moat
Moat lives on a `segment` node, not the parent company, so different business lines
carry different moats.
```json
{"p":"has_segment","kind":"relation","domain":"company","range":"segment","inverse":"segment_of","card":"many"}
{"p":"moat_kind","kind":"relation","domain":"segment","range":"moat","inverse":"moat_for","card":"many"}
{"p":"moat_strength","kind":"metric","domain":"segment","unit":"score_0_5","card":"many"}
{"s":"AAPL","p":"has_segment","o":"AAPL.services","as_of":"2025-09-28","src":"10k-aapl-2025"}
{"s":"AAPL.services","p":"moat_kind","o":"switching_costs","as_of":"2025-09-28","src":"analyst-moat-2025"}
{"s":"AAPL.services","p":"moat_strength","val":4,"unit":"score_0_5","as_of":"2025-09-28","src":"analyst-moat-2025","conf":0.7}
```

### Import / export dependency (between countries)
Directional, weighted with a metric edge alongside the relation.
```json
{"p":"imports_from","kind":"relation","domain":"country","range":"country","inverse":"exports_to","card":"many"}
{"p":"trade_dependency","kind":"metric","domain":"country","unit":"pct_of_imports","card":"many"}
{"s":"USA","p":"imports_from","o":"TWN","as_of":"2025-12-31","src":"comtrade-2025"}
{"s":"USA","p":"trade_dependency","val":0.31,"unit":"pct_of_imports","as_of":"2025-12-31","src":"comtrade-2025","note":"semiconductors"}
```

### Ownership
Weighted by stake; functional only if you model "ultimate parent".
```json
{"p":"owns","kind":"relation","domain":"company","range":"company","inverse":"owned_by","card":"many"}
{"p":"stake_pct","kind":"metric","domain":"company","unit":"pct","card":"many"}
{"s":"BRK.A","p":"owns","o":"AAPL","as_of":"2026-03-31","src":"13f-brk-q1-2026"}
{"s":"BRK.A","p":"stake_pct","val":5.9,"unit":"pct","as_of":"2026-03-31","src":"13f-brk-q1-2026"}
```

## Exposure tracing (why the structure pays off)
"What is exposed to Taiwan risk?" becomes a traversal, not a memory lookup:
```
python scripts/graph.py neighbors --id TWN --dir in --depth 2
python scripts/graph.py path --from AAPL --to TWN
```
The second resolves chains like `AAPL --supplied_by-- TSMC --country_of_risk-- TWN`,
surfacing indirect exposure two hops deep.
