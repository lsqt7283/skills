# Playbooks

Concrete procedures for the recurring jobs. All commands assume
`python scripts/graph.py --dir <kg> <cmd>`; `--dir` defaults to `kg`.

## A. Capture-from-dialogue (the most common path)

Triggered when normal research conversation produces a durable fact. Do it inline,
before the topic moves on, so the graph genuinely grows with the session.

1. **Resolve nodes.** `find "<name>"` for subject and object. If a node is missing,
   `add-node`; if it exists under another label, add the label to `aka`.
2. **Pick the predicate.** `stats` to see existing predicates. Reuse one that means the
   same thing. Only `add-pred` if genuinely new — give it `domain`, `range`, `inverse`,
   `card`.
3. **Register provenance.** If from a document, `add-src`. If from the conversation,
   reuse/register a `dialogue` source. Never leave `src` blank.
4. **Append edges.** One relation edge; add separate metric edge(s) for any magnitude.
   Set `as_of` to the period the fact describes; add `conf` if inferred.
5. **Lint.** `validate` before considering it done.

Example — "Berkshire holds about 5.9% of Apple as of Q1":
```
python scripts/graph.py find "berkshire"
python scripts/graph.py add-node --id BRK.A --type company --name "Berkshire Hathaway"
python scripts/graph.py add-src  --id 13f-brk-q1-2026 --title "Berkshire 13F Q1 2026" --kind filing --accessed 2026-06-16
python scripts/graph.py add-edge --s BRK.A --p owns --o AAPL --as-of 2026-03-31 --src 13f-brk-q1-2026
python scripts/graph.py add-edge --s BRK.A --p stake_pct --val 5.9 --unit pct --as-of 2026-03-31 --src 13f-brk-q1-2026
```

## B. Bulk ingest (filing, transcript, note)

1. Register the document once with `add-src`.
2. Sweep for entities → `add-node` (dedupe via `find` first).
3. Sweep for relations and metrics → `add-edge`, all sharing the one `src` and the
   document's reporting `as_of`.
4. Introduce any new predicates with `add-pred` *before* the edges that use them.
5. Optionally `archive-src` to store the salient extracted text (playbook E).
6. `validate`, then `stats` to sanity-check the shape.

## C. Query / traverse (lightweight by design)

| question | command |
|----------|---------|
| who/what is this? | `find "<text>"` |
| direct relationships | `neighbors --id X --depth 1` |
| who depends on X (incoming) | `neighbors --id X --dir in` |
| indirect exposure | `path --from X --to Y --max-hops 4` |
| build a dossier (machine) | `subgraph --id X --depth 2` |
| graph health/size | `stats` |

For an exposure screen ("what touches Taiwan?"), combine an incoming-neighbor sweep with
targeted `path` calls. Doc pointers appear as edges; pass `--pred` to restrict traversal
to entity relations (e.g. `--pred supplies_to`) when you don't want document links.

## D. Attach a source excerpt (OKF content layer)

When you want to keep the *text* of a snippet — an excerpt, a note, a memo — without
bloating the graph, attach it as an OKF doc and point entities at it. The graph stores
only a pointer; the prose lives in `docs/`.

One excerpt, shared by two companies:
```
python scripts/graph.py attach-doc \
  --id excerpts/tsmc-aapl-dependency --type excerpt \
  --title "TSMC-Apple capacity dependency" \
  --for AAPL --for TSMC --rel has_excerpt \
  --src 10k-aapl-2025 \
  --desc "Apple is TSMC's largest advanced-node customer." \
  --tags supply_chain --tags taiwan \
  --body "Per the FY25 10-K, substantially all of Apple's leading-edge silicon is fabricated by TSMC, predominantly in Taiwan."
```
This writes `docs/excerpts/tsmc-aapl-dependency.md` (conformant OKF), registers a doc
node + a source, auto-registers `has_excerpt` if new, and adds a reference edge from each
`--for` entity. For long material use `--body-file path/to/excerpt.txt`. Attach the same
`--id` from another entity later to share it further; attach different ids to give one
entity many docs. Read on demand: `read-doc --id excerpts/tsmc-aapl-dependency`.

## E. Archive source text (durable provenance)

When a source might vanish or change — paywalled article, editable web page, transcript
that gets pulled — archive its salient text so the citation stays verifiable. This stores
the extracted text as a first-class OKF concept under `docs/references/` and records the
path on the `sources.jsonl` entry.
```
python scripts/graph.py archive-src \
  --id 10k-aapl-2025 --title "Apple FY25 10-K" \
  --url "https://sec.gov/aapl10k" --kind filing --accessed 2026-06-16 \
  --desc "Risk factors + supplier concentration extract" \
  --body-file extract.txt
```
After export, every dossier that cites `10k-aapl-2025` links its citation to
`/references/10k-aapl-2025.md` (with the original url noted alongside). `archive-src` both
creates the source if new and enriches it if it already exists. Re-run anytime to refresh
the archived text; `validate` warns if a recorded `archive` file goes missing.

## F. Export an OKF bundle

Regenerate the human-readable view from the graph:
```
python scripts/graph.py export-okf            # writes to kg/docs/
python scripts/graph.py export-okf --out /some/where/bundle
```
Produces one OKF-conformant dossier per entity (`<type>/<id>.md`: Title-cased `type`,
`resource: kg://node/<id>` back-pointer, then Relationships, Metrics, Documents,
Referenced-by, Citations), per-directory `index.md`, a `references/index.md` for archived
sources, a root `index.md` carrying `okf_version`, and a dated `log.md` entry. **Attached
docs and archived sources are never overwritten** — only dossiers and indexes regenerate,
so it is safe to run after every batch. The bundle is plain markdown: `cat`, `grep`,
`git`, or hand it to any OKF-aware tool.

## G. Reconciliation — contradiction vs. time series

The rule that keeps the graph honest as facts change.

- **Same fact, different `as_of` → time series.** Keep every record. A company's
  `country_of_risk` moving from `CHN` (2024) to `VNM` (2026) is two valid edges. Queries
  read the latest `as_of` unless asked for history.
- **Conflicting objects, same `as_of`, functional predicate (`card:"one"`) → real
  contradiction.** `validate` flags it. Resolve by checking sources, preferring the
  higher-quality `src`, and appending a corrected record (don't delete — preserve the
  history of what you believed).
- **Same fact, same `as_of`, `card:"many"` → not a conflict.** A company legitimately
  `supplies_to` many customers, and legitimately `has_excerpt` many docs.

When two sources disagree on a metric at the same `as_of`, keep both edges (different
`src`) and annotate with `conf`; let downstream analysis weigh them.

## H. Maintaining the ontology

Periodically: `stats` lists predicate counts. If two predicates mean the same thing, pick
the canonical one, append corrected edges using it, and stop using the synonym. The fuzzy
check in `validate` ("did you mean …?") is the early-warning system — treat its
suggestions as a prompt to consolidate, not just to silence the error.
