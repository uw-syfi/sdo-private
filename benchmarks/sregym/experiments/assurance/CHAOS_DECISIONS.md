# Scripted responder, chaos and soak: decisions log

Goal: high assurance, with no LLM quota spent, that the reimplemented prototype's full incident path is solid. A deterministic scripted responder replaces only the model; the real controller, responder Job, broker, reflection, closure gate, receipts and run records run unchanged. Branch `vic/feat/scripted-responder-chaos`, based on `origin/vic/exp/feedback-loop-e2e` at `f0ae5c2`. Each entry states what was chosen, the alternatives, and why.

## Where the model is replaced

- **Decision: replace the `codex` binary, not an SDO interface.** `benchmarks/sregym/assurance/scripted_codex/` is a stdlib-only stand-in for the Codex CLI. It speaks `codex exec --json` (the events agentshim's real Codex parser reads) and writes the session rollout under `$CODEX_HOME/sessions` (the `token_count` events SDO's accounting and `incident_cost` read). It is installed into test images only (`Dockerfile.scripted`, tags `:assure`), where it replaces the real `codex` and `claude` CLIs.
  - Everything above it is real: agentshim's host executor and stream parser, `run_structured_turn` (rollout-delta accounting, `SDO_TURN_USAGE_LOG`), `execute_incident`, the responder Job and result ConfigMap, the Go controller's dispatch and closure gate, the broker's proposal/outcome/reflection commits, validator Jobs, controller rollout of learned detectors, receipts and the fast loop's records.
  - Alternatives rejected:
    - a new `SDO_AGENT_PROVIDER=scripted` backend in `libs/agent_cli` or `sdo/`: puts a test double in production paths, and skips agentshim, rollout accounting and session resume, where several past bugs were (double-counted resumed reflection tokens);
    - agentshim's `FakeExecutor`: in-process only; it cannot run inside the responder Job and the controller pod.
  - No production file changed for the scripted responder. Production code does not import it.
- **Faithful quirks.** Like the real CLI, `turn.completed` reports the session's *cumulative* usage (so a resumed reflection reports the responder's tokens too), and `codex exec resume` of a session with no rollout fails with `thread/resume failed: no rollout found`. The usage per model response is a deterministic function of the directive seed, the turn kind and the response index, with distinct offsets per kind, so a double-counted or dropped turn is a mismatch, never noise.
- **Model id and provider.** Every scripted run uses provider `codex` and model `gpt-6-luna` (the coordinator's 2026-09-28 rule), so the usage records have the real Codex shape. No Claude or other-provider path was added.
- **Nothing can reach a model.**
  - the scripted images contain no model CLI (`build_images.sh` checks `! command -v claude`);
  - the scripted CLI refuses any turn that is not an SDO responder or reflection turn (exit 3, "no scripted plan"), so an unplanned model call fails loudly instead of being faked;
  - the harness's host lifecycle is reuse-only: it raises instead of falling back to a model-backed lifecycle;
  - the harness process points `HOME` and `CODEX_HOME` at an empty directory and sets a placeholder `OPENAI_API_KEY`, so `install_controller`, which copies host credentials into a missing Secret, can only copy the placeholder. The SREGym worker keeps the original `HOME` (uv cache).
  - `assurance run` refuses images without the `sdo.dev/scripted-codex=true` label.

## How a scripted incident is planned

- **Directive per incident, bound at first use.** The harness writes ConfigMap `sdo-scripted-directive` in `<app>-sdo` just before it injects the fault. The first responder turn copies it into `sdo-scripted-bind-<sha(incident id)>`; restarted responder Jobs and later reflection turns read the binding, so moving on to the next incident can never change an earlier incident's plan.
  - The responder SA may get/create ConfigMaps in the controller namespace (it publishes its result there), and so may the controller pod. The app namespace is untouched, so the controller's configuration diff never sees the harness.
- **The directive names the fault class and target; object names come from live state.** The NetworkPolicy to delete is the one `kubectl get networkpolicy -o json` shows isolating the target; the missing ConfigMap is the Deployment volume that `kubectl get configmap` cannot find, and its manifest is found under `kubernetes/` in the worktree. A plan whose fault live state does not show stops with a failed result.
- **Mitigation modes:** `correct`; `wrong_then_correct` (rollout-restart the target, `sdo incident status` stays unhealthy, then the real repair); `wrong_only_honest` (the restart, then an honest `failed` result); `wrong_only_claimed` (the restart, then a `completed` result claiming a wrong cause and a passing check, the dangerous case).
- **Reflection** writes a real incident detector (Go, with matching and near-miss tests), a playbook with `repair.sh`/`verify.sh`, the manifest entry and the playbook index line, then runs SDO's `memory_check`. Every template passes `memory_check` and `check_cli draft-test` in unit tests. After `wrong_only_claimed` it encodes the *claimed* (wrong) cause, detector `wedged-workload-restart`, because that is what a reflecting agent that believed its fix would do; whether it reaches memory is SDO's job.
- **Turn ledger.** Every scripted turn records `sdo-scripted-turn-<hash>` (kind, session, requests, usage, commands, outcome), which the harness reconciles with receipts, the exported usage logs and the run records.

## Environment

- **Cluster `assure-c0`**, 1 control plane + 1 worker, through `fastloop up --cluster-prefix assure-c --worker-id 0`. The 1+1 default for `fastloop up` is owned by the no-LLM suite branch (`cdb7c7f`, `--kind-worker-nodes`), cherry-picked here as `8bb2f04`; my duplicate `780ca0f` was dropped. `assure-c0` was created with that duplicate before the swap; the topology is the same (1+1). The composite-fault API of the fault driver (`7aa54d5`) is theirs too; this branch does not touch `fault_driver.py` or `environment.py`.
- **Private buildx builder `assure`**, so the shared builders' state is never touched.
- **Seed: lifecycle commit `30e023d`** (the network_policy_block lifecycle seed), copied to `/mnt/data/shli/assure-runs/seed-30e023d`.
- **Images:** `sdo-{controller,sregym-responder}:assure` wrap the `v0.1.0` images rebuilt after `13d5613` (pinned as `:assure-base`); the validator is `v0.1.0` re-tagged `:assure`. Code-level fixes below are rebuilt into new base images before their re-runs.
- **Pushing was denied.** The permission classifier refused `git push` ("Out-of-Place Publication"). Per the rules this was not retried or worked around; commits are local and listed in the final report.
