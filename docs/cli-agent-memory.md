# cli_agent Memory — Design Sketch

> Status: **draft for iteration**. Minimal first version. Deferred ideas are
> listed in §7 so we don't lose them.

## 1. Goal

Give `sregym_agents/cli_agent` a persistent memory that improves diagnosis and
mitigation across incidents, without leaking the held-out SREGym fault catalog.
A new, self-contained subsystem living in `cli_agent/` (not a shared lib, not
reusing crucible's KB).

## 2. The whole idea in one paragraph

After a problem is **confirmed** solved, the driver extracts a short, structured
**lesson** and appends it to a JSONL store. On the next run, the agent can call a
`recall` MCP tool with what it's currently seeing; the store returns relevant
past lessons, framed as *priors to verify, not answers*. Retrieval stays dumb
(filter + return a small set) — the LLM does the reasoning. That's the system.

## 3. Two principles we keep (everything else was optimization)

1. **Verified-only writes.** A lesson is stored only when the environment
   confirmed it (conductor verdict, or symptom-clearance in autonomous mode).
   This is what keeps memory from poisoning itself.
2. **Store the surprise, not the prior.** Capture where reality contradicted the
   obvious guess — the discriminating tell and the confirmed cause — not what the
   LLM already knows.

## 4. The lesson (storage schema)

One JSONL record per verified lesson:

```json
{
  "app": "hotelReservation",
  "role": "db-dependent-startup",          // optional; enables cross-app transfer
  "situation": "frontend 503s; profile pod CrashLoopBackOff; rollout bumped image just before",
  "obvious_guess": "bad image tag",         // the trap (optional)
  "root_cause": "missing DB_HOST env var on profile deployment",
  "tell": "kubectl describe shows env unset; logs show DNS resolve fail on startup",
  "fix": "set DB_HOST on profile deployment env",
  "affected_resource": "deployment/profile.env",  // near-canonical dedup key, from the fix
  "confirmed_by": "verdict",                // "verdict" | "self_verified"
  "seen_count": 1,                          // independent confirmations (lesson reliability)
  "created_at": "2026-05-20T00:00:00Z"
}
```

`seen_count` is the only place we count frequency, and it does **not** violate
principle 6: that ban is on ranking *competing causes* by frequency (a misleading
benchmark prior). Counting *independent confirmations of one lesson* measures the
lesson's reliability — a different quantity, and one worth surfacing on recall.

`situation` deliberately includes **what changed** (rollout/deploy) when
observable — the cheapest, highest-signal anchor, and it doesn't leak the
injected fault label. Everything else is short free text the LLM reasons over.

## 4a. Matching — two different lookups

Finding "the same incident" is two distinct problems; conflating them is what
sank the old flat store.

- **Dedup (write-time): the LLM decides, matching on the confirmed cause/fix, not
  on symptoms.** Dedup is a *semantic-equivalence* judgment (same cause, different
  phrasing) — an LLM strength and a string/embedding weakness, so the LLM is
  authoritative. At v1 scale, pass **all** per-app lessons to the extraction call;
  it returns "new" or "merge into id X." `affected_resource` (derived from the
  fix, e.g. `deployment/profile.env`) is a **strong signal the LLM weighs, not a
  gate** — it's LLM-derived and noisy, so it must not pre-exclude the true match.
  A structural/embedding pre-filter is needed only at large scale, to narrow
  candidates *before* the LLM (recall-maximizing, never the final decision) —
  deferred.
- **Recall (read-time): match on `situation`** — the ambiguous side, since the
  cause isn't known yet. Deliberately **high-recall, low-precision**: filter by
  `app` (+`role`), return the set (including *competing* causes that share the
  situation), and let the agent verify. Never collapse to a single match.

At v1 scale (tens of lessons per app) this needs **no index and no embeddings** —
load per-app and let the LLM judge. Embedding-threshold matching is the fragile
mechanism that broke the flat store; defer it until per-app counts are large.

**Scaling note.** Passing all candidates to the LLM is O(N) tokens/write (~O(N²)
over a run), so LLM dedup does not scale on its own. What saves v1 is that **N is
the count of distinct failure modes, not incidents** — dedup keeps the store sized
to distinct root causes, which saturates, so per-app N plateaus at tens. It breaks
only for a shared/global store, a large distinct-cause space, or pre-saturation.
The scale path (deferred): **two-stage dedup** — narrow to top-k candidates by
embedding on the confirmed `root_cause`/`affected_resource` (safe: specific, not
the ambiguous *symptom* embedding that broke the flat store), then the LLM
confirms among those. The LLM stays the judge; the index only bounds how many it
sees, making cost per write ~constant.

## 5. Read path

A single MCP tool, appended to the driver's `extra_mcp_servers`:

- `recall(situation: str) -> list[lesson]` — filters by app (and `role` if set),
  returns the matching lessons (return all when the store is small; cap later).
  The tool's response wraps them with framing: *"Past incidents that may relate.
  Treat as hypotheses to verify against the live cluster, not answers."*

No start-of-run prompt injection in v1 — one mechanism only. (Note the
provider floor: `recall` works for claude/codex; gemini/opencode lack MCP, so
they simply run without memory. Acceptable for v1.)

## 6. Write path

Insert and dedup are the **same event** — one upsert per problem, with dedup
inline. There is no separate dedup pass, and `recall` never writes. Done
out-of-band in the **driver**, after the session returns and the outcome is known
(mirrors how the driver already inspects `final_stage`):

1. If **not confirmed** → write nothing.
2. One LLM call extracts a lesson (the §4 schema) from the run transcript, and is
   given the existing lessons for this app so it can **upsert**:
   - **no match** → emit a new lesson (`seen_count: 1`);
   - **matches an existing root cause** → return a merged version of that lesson:
     `seen_count += 1`, and broaden `situation`/`tell` to cover the new
     presentation so future recall fires more reliably.
3. Load → upsert → rewrite the store (small enough to rewrite wholesale).

Matching is **conservative**: a high bar on root-cause match, and when unsure,
create a new lesson. A stray duplicate is just noise; a *false merge* corrupts a
lesson, so we bias away from it. Contradiction handling (a lesson that later
proves wrong) stays deferred — that's the falsifiability work.

No surprise-filter or distillation in v1.

## 7. Deliberately deferred

Kept out of v1 to stay simple; revisit if v1 shows value:

- **Surprise filter** (LLM judgment to skip lessons the prior already covers).
- **Supersede / contradiction handling** (a lesson that later proves wrong);
  periodic **distillation**. (Basic merge-on-write *is* in v1 — see §6.)
- **Re-validation** of stale lessons against the live cluster.
- **Learned role invariants / behavioral envelopes** from healthy state.
- **Structural fingerprint keys** (spec/status diff, dependency-graph position).
- **Specificity-based ranking**; richer retrieval than filter-and-return.
- **Start-of-run injection** as a second, provider-portable channel.
- **Cross-app / per-role transfer** (principle 7): shared store with per-role
  dedup. Deferred by the per-app scope decision; this is the main v2 lever for
  cold-start and rare/novel faults.

## 8. Open questions

- **Store scope** — **DECIDED: per-app for v1.** No cross-app transfer; dedup
  candidate sets stay small (distinct failure modes per app). `role` becomes
  optional and is not needed for matching. Store location must still be outside
  the ephemeral `SREGYM_EXP_ENV` workdir; keyed by `app`.
- **Lesson extraction** — **DECIDED: resume the agent session.** Rather than a
  separate model reading a reconstructed transcript, the driver issues a second
  `generate()` on the *same* `agentshim` session after the verdict; the agent's
  full investigation is already in its context window (prompt-cached on resume).
  Extraction uses the run's own provider/model by construction. One resume turn
  does extraction **and** dedup (given the existing per-app lessons).
- **`role` assignment** — **DECIDED: omit in v1.** Only needed for cross-app
  transfer, which per-app scope defers. Revisit when adding a shared store with
  per-role dedup.
- **Autonomous-mode confirmation** — **DECIDED: only full completion.**
  Autonomous mode has no per-stage grader, so a write fires only on a completed
  run (`confirmed_by="self_verified"`); no partial writes in autonomous mode.
- **Partial confirmation** — **DECIDED: write it (default mode).** When the
  conductor stage advanced to `mitigation` but never reached terminal, diagnosis
  was accepted while the fix was not: store the lesson with
  `confirmed_by="diagnosis_only"`, recording `fix` as unverified.

## 10. Implementation notes (v1)

Lives in `sregym_agents/cli_agent/memory/`: `store.py` (per-app JSONL
`LessonStore`), `extract.py` (resume-turn prompt + reply parsing + upsert),
`recall_server.py` (read-only `recall` FastMCP tool). Wired in
`sregym_agents/cli_agent/driver.py`; store-dir defaulting in
`libs/sregym_lib/runner.py` (`_inject_memory_defaults`).

Two deviations from the original sketch, both simplifications:
- **The `recall` server runs in-process** in the driver (background uvicorn
  thread, ephemeral port), not as a forked daemon with PID files / agents.yaml
  hooks. The old daemon existed because the SQLite store needed one shared
  writer; the JSONL store is written directly by the driver, so the server is
  read-only and per-run.
- **Partial confirmation is inferred from the conductor stage** (`mitigation`
  reached but not terminal) since the conductor exposes only a current-stage
  string, not per-stage verdicts.

**Enabling:** set `memory_enabled = true` in the `[agent.cli_agent]` config
block (optionally `memory_dir`). Off by default until validated. The store dir
defaults to the experiment/pipeline root (`<exp_dir>/memory`) so lessons
accumulate across a run's per-problem processes; for direct CLI invocation it
defaults to `~/.sds/cli_agent_memory`.

## 9. How we'll know it works

- A lesson written after solving problem A is recalled and visibly used on a
  later related problem.
- No unconfirmed run ever produces a write.
- Net: same-or-better solve rate with memory enabled vs the current baseline.
