# Simultaneous composites: decisions and results

Status: 2026-10-01. Branch `vic/exp/simultaneous-composites` (from `vic/exp/composite-learning-curve`). Predecessor: `docs/composite-learning-curve-decisions.md`.

## Question

Memory showed no benefit on composites because faults are injected over about 16 s and the controller dispatches about 1 s after the first finding, so learned detectors for later faults can only fire after dispatch. If every component fault is present before the controller first observes the namespace, do learned detectors for all faults fire before dispatch, does the first request carry every finding, and do repeats get faster and need no follow-up?

## Decision: how to inject simultaneously

Options: (a) scale the controller to 0 and back, (b) inject all, then install the controller, (c) use the controller's existing maintenance (pause/resume) switch.

Chosen: (c), as an opt-in `--inject-before-resume` flag on `fastloop run`, implemented by `InjectBeforeResumeOps` (`benchmarks/sregym/fastloop/simultaneous.py`), a `ClusterOps` wrapper. The persistent stage normally resumes the controller, waits for an all-clear baseline, then injects. With the wrapper, the stage's resume is deferred: the controller is paused (new maintenance generation, acknowledged in the controller log), the whole composite is injected, then the controller is resumed. Per `set_controller_maintenance`, a resume makes the controller re-list the namespace and evaluate every detector once, so all faults are seen in the first evaluation. Reasons: no production change and no image rebuild; works for the first composite (fresh install) and later ones (reuse); the persistent controller, its memory and incident accounting stay untouched. Rejected: scale to 0 (loses the controller pod identity the persistent state checks) and inject-then-install (only works for the first composite). Sequential injection stays the default. The baseline all-clear wait is skipped in this mode (the baseline is by construction not clear).

Caveat: injecting still takes about 16 s, but during it the controller is paused; the clock for inject->mitigation starts after injection finishes, as before.
