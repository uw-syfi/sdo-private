# Configuration boundaries

The production SDO lifecycle does not expose switches for the removed analysis, shell-script generation, bounded monitoring, or standalone fault-injection phases. The public interface is:

```text
sdo operate REPOSITORY --namespace NAMESPACE (--goal TEXT | --goal-file PATH)
  [--application NAME] [--model MODEL]
  [--controller-image IMAGE] [--responder-image IMAGE] [--validator-image IMAGE]
  [--repository-pvc PVC] [--credentials-secret SECRET]
  [--attempts N] [--timeout-seconds N]
```

Use `sdo operate --help` for descriptions and current defaults.

## SRE Gym experiment configuration

SRE Gym configuration remains benchmark-specific. The source of truth is `sregym_agents/experiment_config.py`, with complete examples under `sregym_agents/experiments/`.

Common sections are:

| Section | Purpose |
|---|---|
| `[runner]` | Agent, model, parallelism, problem selection, summaries, and application-workspace behavior |
| `[runner.variants]` | Variant generation, ordering, limits, and seeds |
| `[runner.env]` | Benchmark worker and judge environment |
| `[agent.crucible]` | Crucible diagnosis, mitigation, memory, reflection, and iteration settings |
| `[agent.sdo_codex]` | SDO benchmark-adapter model and timeout settings |
| `[pipeline]`, `[defaults]`, `[[stages]]` | Multi-stage experiments and per-stage overrides |

Do not move benchmark options into production configuration. In particular, benchmark results, fault identities, judge models, submission limits, task lists, and variant controls must remain behind the SRE Gym boundary.

Configuration snapshots emitted by benchmark runs are preferable to the original input file when analyzing results because they record resolved defaults and stage overrides.
