# Few-Shot Trajectory Retrieval for SREGym Agents

Reduce agent investigation time by injecting a compressed prior incident as a worked
example at the start of each run. The agent uses it as a targeted checklist rather
than starting from a broad exploratory sweep.

## Core Loop

1. **Post-run**: compress each successful trajectory into a structured case summary and store it with an embedding.
2. **Pre-run**: embed an initial health snapshot of the new cluster, retrieve the closest past case, inject it into the prompt.

## Phase 1 — Trajectory Compression (offline)

After each successful run, pass the trajectory JSON to an LLM and produce a structured case file:

```
App: astronomy-shop
Symptoms: cart returning 503, checkout OOMKilled
Key checks:
  - kubectl get pods → cart crashlooping, checkout OOMKilled
  - kubectl logs cart → "connection refused" on valkey
  - redis-cli AUTH → WRONGPASS
  - kubectl get jobs → valkey-memory-flood running
Root causes:
  - valkey requirepass set but cart has no REDIS_PASSWORD
  - memory flood job exhausting 20Mi limit
Fix:
  - Added REDIS_PASSWORD to cart deployment
  - Deleted flood job, raised memory limit
Lesson: for valkey/cart issues, check requirepass and active jobs before app config
```

The **lesson** line is the key reusable artifact — a heuristic, not a hardcoded answer.

Store: `(embedding of symptoms+app, case file)` in a flat vector store (SQLite or cosine over a JSON file is sufficient at this scale).

## Phase 2 — Retrieval Key

Run a fast structured health check at the start of the episode (2–3 kubectl commands), embed the output, retrieve the top-1 case by cosine similarity.

The agent runs these commands anyway, so there is no extra cost — the only addition is the embedding + lookup before the main prompt is sent.

Apply a similarity threshold: if the best match is below the threshold, inject nothing rather than risk misleading the agent with an irrelevant case.

## Phase 3 — Prompt Injection

Inject the retrieved case as a **Prior Incident** block near the top of `session_autonomous.j2`:

```
Prior incident on a similar cluster (for reference only — your cluster may differ):
  Symptoms: cart 503, checkout OOMKilled
  Investigation path: checked valkey auth → requirepass mismatch;
    checked active jobs → memory flood job
  Resolution: added REDIS_PASSWORD, deleted flood job, raised memory limit
  Key lesson: check valkey requirepass and running jobs early for cart failures
```

Framing as "for reference only" prevents the agent from short-circuiting investigation and treating it as the answer.

## Where the Speed Gain Comes From

Slow runs (1000–1400s) pay a broad-sweep tax — the agent runs 15–20 kubectl commands before forming a hypothesis. A good retrieved example collapses this to 3–4 targeted checks, matching the fastest observed runs (~100s).

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Same-problem retrieval = answer leakage | Suppress or strip root-cause section when problem ID matches |
| Wrong retrieval misleads agent | Similarity threshold; inject nothing if confidence is low |
| Compression loses detail or hallucinates | Spot-check case files; keep raw trajectory alongside |
| Overfits to known fault types | Track accuracy on unseen fault types separately |

## Implementation Path

1. **Post-run summarization**: after each run, call an LLM on the trajectory JSON and write a case file next to the results CSV.
2. **Vector store**: flat file or SQLite storing `(problem_id, embedding, case_file_path)`.
3. **Retrieval in driver**: after initial health check, embed output, lookup top case, pass to prompt builder.
4. **Prompt template**: add optional `prior_incident` block to `session_autonomous.j2`.
