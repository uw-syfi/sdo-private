# Follow-up responders for residual findings: decisions

Branch `vic/fix/redispatch-residual-findings`, from `vic/exp/nfault-composites`.

## Problem

With several simultaneous faults the persistent controller dispatched one responder for the first health finding. The responder fixed some faults, a health finding stayed active, and after the verification timeout the controller recorded `detector_review_required` and exited. The Job restart read the same persisted state and exited again. No second responder was dispatched, the incident never closed, and nothing was reflected on.

## Decisions

1. **Opt-in, default off.** `--max-follow-ups N` (default 0) on the controller; `--follow-up-cooldown` (default 30s). With 0 the controller behaves exactly as before, and the existing detector-review test still passes unchanged.
2. **Residual findings are the active findings of health detectors** (from the finding-state tracker, so they include findings that activated after the first dispatch and findings the responder was dispatched for but did not fix). When no health detector exists, the incident's own findings are used, as verification does.
3. **Same open incident, new incident id.** The follow-up replaces the current request (new id, so the Job dispatcher and broker worktree are fresh), keeps the incident open, and resets the responder state. One responder is in flight at a time and one closure is cut, for the last request, when health clears. The original detection and dispatch times are kept, so closure timing still measures from first detection.
4. **Request context.** `IncidentRequest.follow_up` (Go `FollowUpContext`, Python `FollowUpContext`) holds `original_incident_id`, `parent_incident_id`, `attempt`, `max_follow_ups` and a bounded (3000 characters, newest kept) `prior_responder_summary` built deterministically from the earlier request's findings and the result's status, root causes, repairs and error. The request's `findings` are the residual findings only. The responder prompt adds a scoping paragraph only when `follow_up` is set, so prompts are unchanged otherwise.
5. **Bounded and restart-safe.** The attempt count is part of the persisted request, not separate state. A restarted controller restores the same attempt and cannot exceed the budget. When the budget is spent the controller falls back to `detector_review_required`, which is the old behavior (including the restart loop; the wedge is only removed while follow-ups remain).
6. **Cooldown.** A follow-up fires at `completion + min(cooldown, verification timeout)`. It must exceed the health detectors' clear window, otherwise a finding about to clear can trigger a needless follow-up (seen while writing the closure test). The 30 s default is above the default clear window of two evaluations; tune it per detector interval.
7. **Detector and judge boundaries unchanged.** Health detectors stay judge-owned. The controller dispatch is deterministic and calls no LLM. `controller/runtime` gains no transport-specific logic. The SREGym adapter needs no change because it already waits for the next closure with an unknown incident id.

## Known limits

- Repository changes and memory proposals in an earlier incident's isolated worktree are not merged. Only the final (follow-up) incident's worktree is closed through the broker. Cluster repairs from earlier responders persist. With `recorded-actions` (the SREGym adapter) this matters little; with `commit` policy earlier proposed repository changes are dropped.
- The outcome and reflection cover the last responder and carry the chain in `request.follow_up`; the earlier responders' results are visible only through `prior_responder_summary`.
- Controller telemetry does not emit a distinct event for the follow-up; the follow-up's residual findings keep their original `after_dispatch` relation.

## How to enable

- Install config: `ControllerInstallConfig(max_follow_ups=3, follow_up_cooldown_seconds=30)`.
- SREGym: `agent_config.sdo_codex.max_follow_ups = 3` (and optionally `follow_up_cooldown_seconds`).
- Fastloop: `--max-follow-ups 3 [--follow-up-cooldown-seconds 30]`.
- Raw controller: `--max-follow-ups 3 --follow-up-cooldown 30s`.

## Tests

Go (`controller/runtime/controller_followup_test.go`): follow-up dispatched for residual with context and prior summary; bounded across a simulated restart with fallback to detector review; default off; closure of the follow-up chain; negative config rejected. Python: contract field round trip, prompt guidance, install config arguments and validation, SREGym `agent_config` plumbing.

## Images

The `nf2` image build (`SDO_IMAGE_TAG=nf2 BUILDX_BUILDER=sdo-example scripts/build_sdo_images.sh`) was blocked by the tool permission classifier in this session, so no `nf2` images exist yet. Build from the head of this branch before using the flag; the existing `nf1` images do not contain the controller flag and would reject `--max-follow-ups`.
