# Late-firing validation decisions

Branch `vic/exp/late-fire-integration` = `vic/exp/spec-first-detectors` (6058318) + `vic/feat/detector-firing-telemetry` (14dcace).

## D1 Merge
- What: `git merge --no-ff` of the telemetry branch into the spec-first branch.
- Alternatives: rebase, cherry-pick.
- Why: both branch from 9530231; merge keeps both histories. Result: no textual conflicts (broker_service.py and closure models touched different hunks). Prompts for baseline/generalize not touched by telemetry.
