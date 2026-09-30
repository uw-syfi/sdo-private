# Late findings pull (pull-before-act): decisions

Branch `vic/feat/late-findings-pull` (from `vic/feat/detector-firing-telemetry`). Each entry is what / alternatives / why.

1. **Channel: the firing stream on the repository PVC, read directly by the responder.**
   The responder Job mounts the whole repository PVC at `/workspace` (`kubernetes_job_dispatcher.go`, same uid 65532 as the controller), so `/workspace/.sdo-runtime/telemetry/detector-firings.jsonl` is readable from the responder pod with no new transport and no Go change.
   Alternatives: (a) the broker CLI: it is a stdin-JSON service invoked by the controller, not reachable from the responder pod; (b) a per-incident JSON file mirrored by the controller into the worktree: needs a Go change, dirties the worktree the broker inspects, and is a push; (c) a ConfigMap the responder polls: new RBAC and a write path in `controller/runtime`; (d) resuming the session with a follow-up message: changes responder semantics.
   Why: the stream already has incident id, `after_dispatch` relation, bindings, severity and surfaced playbooks; `controller/runtime` stays untouched and transport-neutral.

2. **Reader lives in `sdo/operational_memory/late_findings.py`; `sdo incident late-findings` is an alias.**
   The runtime images have no `sdo` console script (only `PYTHONPATH=/opt/sdo`), and `python3 -m sdo` imports the whole lifecycle. The prompt therefore names `python3 -m sdo.operational_memory.late_findings`; the module is also usable as `sdo incident late-findings` for consistency with `sdo detector check`. The broker and the responder prompt can import it (`tach`: `operational_memory` is a dependency of both).

3. **Scoping.** The command takes `--incident-id`, filters to that incident, `activated` events, `dispatch_relation == after_dispatch`. Batched/before_dispatch records (already in the request), other incidents, and incident-less records are excluded. Honest limit: this is scoping by the command; the responder could `cat` the file, which carries only controller-derived data (no benchmark labels or verdicts) and is no more than the volume already exposes. A reactivated finding keeps one entry with its first `activated_at`; `still_active` is false if a later `cleared` record exists.

4. **Playbook paths are filtered to files that exist in the worktree** (relative, contained, regular files), so a path the repository no longer has (or a crafted one) is never surfaced.

5. **Failure mode: never break the responder.** Missing stream, unreadable/torn lines and receipt write failures yield an empty list (`telemetry_available: false` when no stream) and exit 0; a receipt write failure is a stderr warning. Works the same in `commit` and `recorded-actions` (the command never reads the repair policy).

6. **Receipts: a one-line JSON append beside the stream (`late-findings-pulls.jsonl`), folded into the closure by the broker.**
   Alternatives: have the responder self-report in `IncidentResult` (unreliable, LLM-dependent); count commands in the responder turn log (no emptiness information, unreliable quoting); controller reads it (would add Python-format knowledge to Go).
   Why: deterministic, per pull, records non-empty and the returned detectors/playbooks. The command's only write is this receipt, so "read-only" means no cluster/repository/stream mutation. Broker `process_closure` attaches `BrokerClosure.late_findings_pull` (new optional field, default `None`; old closures load; the ledger keeps the enriched closure) only with `--late-findings pull`. `applied_late_playbooks` is the intersection of returned playbooks and `result.applied_playbooks`.

7. **Receipt fields.** The adapter adds `late_findings_pull` and `late_finding_consumed` (a pull returned something) only when the closure has the evidence, so off-mode receipts are unchanged. A malformed summary becomes `late_findings_pull_error`, never a failure.

8. **Flag plumbing.** `--late-findings {off,pull}` mirrors `--reflection-guidance`: `ControllerInstallConfig.late_findings` (validated in `__post_init__`), broker CLI argument, SREGym driver `agent_config.sdo_codex.late_findings`, fastloop. Unlike reflection guidance, the `off` value emits no controller arguments at all, so off-mode controller args, Job specs and prompts are byte-identical. `pull` emits `--responder-env=SDO_LATE_FINDINGS=pull` and `--broker-arg=--late-findings pull`; the responder reads the environment, so the same responder image serves both arms.

9. **Guidance wording** is one short paragraph (run once now and before the first change to the cluster or repository; playbooks are hypotheses to confirm against live state, never replayed blindly; an empty list means nothing more has arrived). It is scanned by the same denylist as the reflection guidance (`test_late_findings_guidance.py`, reusing `_leaks`).

10. **Commits and pushes were denied by the tool permission classifier** ("Out-of-Place Publication") for the first commit/push attempt and again for a plain local commit. I did not retry or work around it; all changes are left uncommitted in `/mnt/data/shli/sdo-worktrees/pullfind` on branch `vic/feat/late-findings-pull` for the user to commit and push.

11. **Not done:** no Go change (none was needed; the existing Go tests were still run); no cluster, agent run or image build; no fallback that counts pulls from the turn log when the receipt cannot be written; the submodule `third_party/sregym` was initialised locally (local URL, 2933dbe) and is not committed.
