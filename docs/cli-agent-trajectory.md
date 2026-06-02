# cli_agent trajectory recording + retrieval

Optional, opt-in. Records each cli_agent run's **raw turn-by-turn trajectory**
(thinking, tool calls, tool results, usage) to a per-app JSONL store that
accumulates across runs, and — optionally — exposes a `search_trajectories` MCP
tool so a later run can retrieve relevant past investigations.

This is a different artifact from [`cli-agent-memory.md`](cli-agent-memory.md):
memory stores one LLM-**distilled** `Lesson` per run; trajectories store the
**unedited** run. Use trajectories when you want the agent to see *how* a past
incident was actually worked, not just a one-line lesson.

## Two retrieval modes (`trajectory_retrieval_mode`)

Both are **MCP tools** (`search_trajectories` + `read_trajectory`), so every
benchmark-agent call is logged server-side and verifiable. They differ only in
*how* `search_trajectories` finds relevant runs:

- **`llm`** — an LLM does the searching. The tool hands the candidate past runs
  (their reasoning + tool sequence + outcome) to an in-tool sub-agent which reads
  them and synthesises the relevant ones. By default the sub-agent is the **same
  agentshim agent as the cli_agent** (its `provider`/`model`, e.g. the `claude`
  CLI) — so search shares the benchmark agent's auth and behaviour. Set
  `trajectory_llm_search_model` / `$SDS_TRAJECTORY_LLM_SEARCH_MODEL` to a litellm
  id (e.g. `vertex_ai/gemini-2.5-flash`) to override with a distinct/cheaper
  model. If the search call fails (or no model/provider resolves), the tool falls
  back to returning the candidate runs unranked.
- **`rag`** — embedding retrieval. The tool embeds the query and returns the
  top-`k` past-run digests by cosine similarity. Uses litellm embeddings when
  `SDS_TRAJECTORY_EMBED_MODEL` is set (e.g. `text-embedding-3-small`), otherwise
  a deterministic offline hashing embedder (lexical, no API key — fine for
  smoke tests, weaker semantically).
- **`off`** — record only; no retrieval exposed to the agent.

Both need an MCP-capable provider (`claude`, `codex`). The earlier
filesystem-browse design for `llm` mode was dropped: a capable agent reads files
inside invisible sub-agents, which makes retrieval unverifiable — routing it
through the MCP tool fixes that.

In every mode the in-flight run is invisible to retrieval/browse: it is written
to a `.jsonl.partial` staging file and atomically committed to `.jsonl` only on
close, so an agent never reads its own partial run and a `*.jsonl` listing shows
only completed prior runs.

## What retrieval ranks on: the digest

Retrieval never embeds/searches the raw run — it operates on a compact
**digest** per run (`TrajectoryStore.digest`). By default the digest is built
from run *mechanics*: `app` + outcome + the tool-call name sequence + a window of
the agent's reasoning. That ranks poorly against a symptom-shaped query, because
the query ("frontend 503s, profile has no endpoints after a deploy") and the
document ("tools: kubectl, kubectl, grep") live in different registers.

### Structured findings (`trajectory_record_findings`)

When enabled, the driver exposes a **`record_findings` MCP tool** (server name
`trajectory_record`) and the prompt instructs the agent to call it once, near the
end, with a structured summary of its run:

| field | meaning |
| --- | --- |
| `situation` | symptoms observed, including any recent rollout/deploy |
| `tell` | the check/output that discriminated the true cause from the wrong guess |
| `root_cause` | the confirmed root cause |
| `fix` | the action that resolved it |
| `affected_resource` | the resource the fix touched, e.g. `deployment/profile` |

These fields (the same schema as a memory `Lesson`) become the digest text, so a
later **symptom query matches a past run's symptoms** — the asymmetry is gone.
The agent still drills into the full transcript via `read_trajectory`; findings
change only *ranking*, not the payload.

Notes:

- **Agent-authored, so leak-safe by construction** — the agent writes its own
  words; no `problem_id` enters (same guarantee as the rest of the file).
- **Last write wins** — a revised `record_findings` call supersedes the earlier
  one.
- **Fallback** — a run with no findings (or an empty call), e.g. a crashed or
  unconfirmed run, keeps the mechanics digest and stays retrievable.
- **Set it at the pipeline `[defaults]` level**, not per-stage: the digest is
  *produced* in the record stage (`trajectory_retrieval_mode = "off"`) and
  *consumed* in a later retrieve stage, so the recording run is the one that must
  expose the tool.
- The `record_findings` server and the `search_trajectories` retrieval server use
  distinct MCP names, so both coexist when a recording run also retrieves.

## Where it's stored

Like the lesson store, the trajectory store **must live outside the ephemeral
`SREGYM_EXP_ENV` workdir** (wiped per problem) so runs accumulate. Layout:

```
{trajectory_dir}/{app-slug}/{problem-slug}__{ts}.jsonl
```

Resolution (`trajectory_dir`):

- **Unset, via the experiment runner** → defaults to the **experiment/pipeline
  root**: `{exp_or_pipeline_dir}/trajectories/` (`_inject_trajectory_defaults`
  in `libs/sregym_lib/runner.py`). A pipeline shares one store at its root, so a
  later stage retrieves earlier stages' runs.
- **Unset, standalone** → `~/.sds/cli_agent_trajectories`.
- **Set** → used verbatim; a relative path is anchored to the cwd *before* the
  driver chdir's into the ephemeral workdir.

For **cross-experiment accumulation** (one growing corpus over many
experiments), set an explicit absolute `trajectory_dir`, e.g.
`~/.sds/cli_agent_trajectories`, in every experiment.

The in-flight run is written into the store but **excluded** from its own
retrieval results (`exclude_path`), so an agent never retrieves itself.

## Enable it

In the `[agent.cli_agent]` block of an experiment TOML:

```toml
[agent.cli_agent]
trajectory_enabled = true
trajectory_retrieval_mode = "rag"   # "off" | "llm" | "rag"
trajectory_record_findings = true   # agent emits a structured digest via record_findings
# trajectory_dir = "~/.sds/cli_agent_trajectories"   # optional; for cross-experiment accumulation
```

Or via CLI flags on the driver: `--trajectory-enabled`,
`--trajectory-retrieval-mode {off,llm,rag}`, `--trajectory-record-findings`,
`--trajectory-dir PATH`.

A common workflow: run a baseline set with `trajectory_retrieval_mode = "off"`
to build the corpus, then a second experiment with `"llm"` or `"rag"` pointed at
the same `trajectory_dir` to measure the lift from retrieval.

Recording works for any provider. `llm` retrieval needs a provider with file
tools (`claude`, `codex`); `rag` retrieval additionally needs MCP support
(`claude`, `codex`).

End-to-end example configs (two stages, same problem — record then retrieve):
`sregym_agents/experiments/cli_agent_trajectory_llm_e2e.toml` and
`cli_agent_trajectory_rag_e2e.toml`. Run with
`uv run python -m sregym_agents.run_sregym sregym_agents/experiments/cli_agent_trajectory_rag_e2e.toml`.

## Record schema (JSONL)

Line 0 is a `meta` record; the body is one `event` record per agent event
(`thinking` / `tool_call` / `tool_result` / `usage`) in arrival order; an
optional `findings` record holds the agent's structured summary (when
`trajectory_record_findings` is on); an optional trailing `summary` holds the
outcome (`completed` / `crashed` / a stage name). Append-only and torn-write
tolerant. Unlike memory, a trajectory is recorded **unconditionally** (even on
crash) — it is a corpus entry, not a verified lesson.

### No answer leak

A retrieving agent reads these files, so the trajectory must not hand it the
benchmark's answer key. The SREGym `problem_id` is answer-leaking (e.g.
`wrong_service_selector_...`) — and so the driver already omits it from prompts.
The trajectory store applies the same rule:

- The agent-readable file's `meta` carries only **`app`, `ts`, `run_id`** (a hash).
  `problem_id`, `provider`, and `model` are **never** written into it.
- Filenames are the `run_id` hash, not the problem slug.
- Digests and `read_trajectory` output surface `run_id`, never `problem_id`.
- The leaking identity is kept only in an analysis-only sidecar,
  `{store}/.index/{app}.index` — outside the per-app directory the agent browses,
  and with a non-`.jsonl` suffix so it is never listed, read, or retrieved.

The recorded *investigation itself* (the agent's tool calls and reasoning, which
naturally include the root cause it found) is preserved — that is the retrieval
signal. Only the benchmark's ground-truth label is withheld.

## Code

- `sregym_agents/cli_agent/trajectory/store.py` — `TrajectoryStore`,
  `TrajectoryRecorder` (agentshim event handler), `TrajectoryDigest`,
  `TrajectoryFindings` (the structured digest schema).
- `sregym_agents/cli_agent/trajectory/embedding.py` — pluggable embedder
  (litellm + offline hashing fallback) + cosine top-k.
- `sregym_agents/cli_agent/trajectory/retrieval.py` — `search_trajectories` /
  `read_trajectory` MCP tools + `TrajectoryRetrievalServer`.
- `sregym_agents/cli_agent/trajectory/record_server.py` — `record_findings` MCP
  tool + `RecordFindingsServer`.
- `sregym_agents/cli_agent/trajectory/demo.py` — end-to-end record→retrieve demo
  (`uv run python -m sregym_agents.cli_agent.trajectory.demo`).
- Driver wiring: `sregym_agents/cli_agent/driver.py`; runner default:
  `libs/sregym_lib/runner.py` (`_inject_trajectory_defaults`).
