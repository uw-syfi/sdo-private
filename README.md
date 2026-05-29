TL;DR:
> This repository contains the research project **SDS (Self-Defining Systems)**, which aims to create AI agents that can autonomously manage software systems. The main component, `sds_operator`, can deploy, monitor, and self-heal online applications. It uses a series of AI agents to analyze code, generate deployment scripts, and track application health, while continuously improving its own prompts.

---

# SDS (Self-Defining Systems)

**SDS** is a research project exploring how **AI agents** can autonomously take over systems work — design, implementation, operation, and improvement. This repository starts with one slice of that vision: **autonomous system operation**.

**SDS** targets online applications. **Microservices** are the first class studied; benchmark apps live in `apps/`.

Two components in this repo:

- **`sds_operator`** — the primary research artifact. Deploys applications, self-heals on errors, monitors health, and improves its own prompts over time using trajectory data.
- **`lego_agent`** — an experimental agent workflow generator, designed as potential shared infrastructure across future **SDS** components.

---

## How the Operator Works

```
sds_operator run <app>
  ├── CodeAnalyzerAgent  → reads codebase → .sds/code_analysis.md
  ├── DeploymentAgent    → generates deploy.sh, self-heals on errors, retries
  ├── AppMonitor         → periodic health checks after deploy succeeds
  └── Trajectory recorder → .sds/trajectories/*.json
```

All agents share a single **LLM provider** (gemini, claude, codex…) configured in `sds.toml` and accessed via `agentshim/`. Provider choice and runtime choice are independent — switching from Claude to Gemini or from **`cli_agent`** to **`pydantic_ai`** requires only editing `sds.toml`.

**Trajectories** are structured JSON recordings of every agent call — the raw material for offline prompt optimization with **DSPy**.

---

## Quick Start

```bash
git clone --recursive git@gitlab.cs.washington.edu:syslab/sds.git
cd sds
uv sync
cp sds.example.toml sds.toml
```

Set your **API key** in `.env` (e.g., `GOOGLE_API_KEY=...` for Gemini or `ANTHROPIC_API_KEY=...` for Claude), then run:

```bash
./sds_operator run apps/deathstarbench/hotelReservation
```

The operator will analyze the codebase, generate **`deploy.sh`** and **`health_check.sh`**, attempt deployment, self-correct any errors, and then monitor the running application. All output lands in `.sds/` inside the app directory.

---

## Configuration

Minimal **`sds.toml`** for the first week:

```toml
[agent]
provider = "gemini"   # gemini | claude | codex | openai | rlm
model = "gemini-1.5-pro"

[runtime]
impl = "cli_agent"    # cli_agent (default) | pydantic_ai
```

Full schema in `app_operator/config.py`. Provider credentials, runtime tradeoffs, and all other fields are in `docs/architecture.md`.

---

## Key Commands

| Command | What it does |
|---|---|
| `./sds_operator run <app>` | Deploy and monitor an application |
| `./sds_operator init-exp <app> <name>` | Create an isolated experiment copy |
| `./sds_operator run-exp <name>` | Run multiple experiments in parallel |
| `./sds_operator analyze-prompts` | Report trajectory metrics |
| `./sds_operator optimize-prompts` | Run **DSPy** offline **prompt optimization** |
| `./sds_lego_agent --prompt "..."` | Generate and run an agent workflow (CLI) |
| `uv run sds-observer-check test --app <app>` | Validate app-authored `.sds/diagnostics` detectors without rolling out an observer |
| `./scripts/start_lego_ui.sh` | Launch the **`lego_agent`** web UI |

Full option reference for each command is in `docs/architecture.md`.

---

## Understanding the Output

After `sds_operator run`, the app directory contains a `.sds/` folder:

```
.sds/
├── deploy.sh            # AI-generated deployment script
├── health_check.sh      # AI-generated health check script
├── diagnostics/         # Optional observer detector code and manifest
├── code_analysis.md     # CodeAnalyzerAgent output (feeds DeploymentAgent)
├── logs/                # Per-attempt logs for deployment and monitoring
└── trajectories/        # JSON recordings of every agent call
    └── *.json           # One file per run; used for analyze-prompts / optimize-prompts
```

Trajectory files contain phase, prompt, response, token counts, and success/failure for each agent call. They are the input to **DSPy** **prompt optimization**.

---

## Going Deeper

| Question | Document |
|---|---|
| How do runtimes, providers, and agents relate? | `docs/architecture.md` |
| How do I optimize prompts with **DSPy**? | `docs/dspy-optimization.md` |
| How do I use **`lego_agent`**? | `docs/lego-agent.md` |
| How do I inject faults? | `docs/fault-injection.md` |
| How do I use RLM for large logs? | `docs/rlm-integration.md` |
| How do I write tests? | `docs/testing-guide.md` |
| What feature flags are available? | `docs/feature-flags.md` |
