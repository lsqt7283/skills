# Playbooks

Concrete procedures for the three recurring jobs. All commands assume
`python scripts/graph.py --dir <kg> <cmd>`; `--dir` defaults to `kg`.

## A. Capture-from-dialogue (the most common path)

Triggered when normal research conversation produces a durable fact. Do it inline,
before the topic moves on, so the graph genuinely grows with the session.

1. **Resolve nodes.** `find "<name>"` for subject and object. If a node is missing,
   `add-node`; if it exists under another label, add the label to `aka` (append a new
   node record with the extended `aka` — last record wins).
2. **Pick the predicate.** `stats` to see existing predicates. Reuse one that means the
   same thing. Only `add-pred` if genuinely new — and give it `domain`, `range`,
   `inverse`, `card`.
3. **Register provenance.** If from a document, `add-src`. If from the conversation,
   reuse/register a `dialogue` source. Never leave `src` blank.
4. **Append edges.** One relation edge; add separate metric edge(s) for any magnitude.
   Set `as_of` to the period the fact describes; add `conf` if inferred.
5. **Lint.** `validate` before considering it done.

Example — user says "Berkshire holds about 5.9% of Apple as of Q1":
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
4. Introduce any new predicates with `add-pred` *before* the edges that use them, so
   `validate` stays clean.
5. `validate`, then `stats` to sanity-check the shape.

Batch tip: generate the `add-edge` calls in one shell block; the store is append-only so
order only matters in that predicates must be registered before use.

## C. Query / traverse

| question | command |
|----------|---------|
| who/what is this? | `find "<text>"` |
| direct relationships | `neighbors --id X --depth 1` |
| who depends on X (incoming) | `neighbors --id X --dir in` |
| indirect exposure | `path --from X --to Y --max-hops 4` |
| build a dossier | `subgraph --id X --depth 2` → feed JSON to analysis |
| graph health/size | `stats` |

For an exposure screen ("what touches Taiwan?"), combine an incoming-neighbor sweep with
targeted `path` calls from each portfolio name to the risk node.

## D. Reconciliation — contradiction vs. time series

This is the rule that keeps the graph honest as facts change.

- **Same fact, different `as_of` → time series.** Keep every record. A company's
  `country_of_risk` moving from `CHN` (2024) to `VNM` (2026) is two valid edges. Queries
  should read the latest `as_of` unless asked for history.
- **Conflicting objects, same `as_of`, functional predicate (`card:"one"`) → real
  contradiction.** `validate` flags it. Resolve by: checking sources, preferring the
  higher-quality `src`, and either correcting the wrong edge's `as_of`/`o` (append a
  corrected record) or lowering `conf`. Do not silently delete — append the correction so
  the history of what you believed is preserved.
- **Same fact, same `as_of`, `card:"many"` → not a conflict.** A company legitimately
  `supplies_to` many customers.

When two sources disagree on a metric at the same `as_of`, keep both edges (different
`src`) and annotate with `conf`; let downstream analysis weigh them rather than
destroying information.

## E. Maintaining the ontology

Periodically: `stats` lists predicate counts. If you spot two predicates that mean the
same thing, pick the canonical one, append corrected edges using it, and stop using the
synonym. The fuzzy check in `validate` ("did you mean …?") is the early-warning system —
treat its suggestions as a prompt to consolidate, not just to silence the error.
