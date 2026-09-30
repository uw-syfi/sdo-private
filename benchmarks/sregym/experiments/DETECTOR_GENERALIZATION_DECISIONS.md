# Detector generalization: decisions log

Branch `vic/exp/detector-generalization` (worktree `/mnt/data/shli/sdo-worktrees/detgen`), from `vic/exp/stream-learning-curve`.

## Phase 0/1 (done)

- **Flag**: `--reflection-guidance {baseline,generalize}`, plumbed through `ControllerInstallConfig.reflection_guidance`, the broker CLI, the SREGym driver (`agent_config.sdo_codex.reflection_guidance`) and fastloop. Alternatives: a separate image or env var (rejected: arms must share images and the install fingerprint must change with the flag). Documented in `docs/feature-flags.md`.
- **Brief** (generalize only): each incident detector gets its Spec description, whether it sets ParameterBindings, and a 1400-char `Detect` excerpt. Memory section moved before the shell-command section and its budget raised to 9000 chars so the global clip drops commands first.
- **Guidance** (generalize only): a five-step generalization protocol (compare with every detector, widen same-class detectors and report via ParameterBindings, parameterize playbook, new detector only for a different cause, keep a near-miss test), replacing the "sharp fault-specific" and "without generalizing" directives. First-time detectors are also asked to key on the class-level condition.
- **Leak scrub (applies to both arms, deviation from "control = current prompt")**: the existing shared wording named fault-tied terms (pod failure reasons, ConfigMap/Secret mount, NetworkPolicies, Service DNS). These were genericized in both arms so the leak test can pass. The trusted SDK API reference (`DETECTOR_SDK_REFERENCE`, shared with the lifecycle) and runtime data (outcome JSON, repository-derived brief content) are exempt from the scan. The control therefore differs slightly from the earlier stream's prompt.
- **Not done**: the broker's "successful reflection must include a detector update unless a learned detector fired in the request findings" check was left unchanged; it may reject playbook-only parameterization when a widened detector fires after dispatch (seen twice in the earlier stream).
- Tests first: `test_reflection_generalization.py` (leak denylist scan of prompt and static brief, guidance text, brief detail, bounding, CLI/driver/install plumbing).

## Blocked before Phase 2

Quota check (reading the newest Codex session rollout under the home `.codex` directory for `used_percent`) was denied by the permission system. The protocol forbids any LLM turn at 97% or more, so without a reading I did not start replay or any live stage. Last known value: 91% (window resets 2026-10-03 18:19 UTC). No cluster, image or LLM turn was started.
