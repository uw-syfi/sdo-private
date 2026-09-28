# Fairness: removing benchmark-tailored knowledge from SDO

Branch `vic/fix/detailor-sdo`, from `fec31e9` (`vic/integrate/assurance-rc2`), 2026-09-28.

## Why

SDO must find fault-relevant checks from the application and its health objective. It must not find them
from hints we wrote knowing the SREGym phase-1 problems. Those problems are:

- S1 `missing_configmap`
- S2 `wrong_service_selector`
- S3 `network_policy_block`
- K1: NetworkPolicy plus the rate ConfigMap
- K2: frontend selector plus readiness probe

The decoys are the `failure-admin-*` ConfigMaps.

A read-only audit found hints in the lifecycle templates and in the health-judge, responder and reflection
prompts. It also found them in the adapter's goal and responder text and in the responder RBAC. Two
directory names leaked the problem id as well. This file records one decision per item.

**Rule.** SDO code, templates and prompts may contain generic operational mechanisms that any SRE operator
would ship. They may not contain anything chosen because it matches a benchmark fault. For each item the
decision is one of these:

- **(a) remove.** The health judge derives the check from the objective and the observed topology.
- **(b) generalize** into a truly generic mechanism.
- **(c) keep**, with a written reason.

Line numbers refer to `fec31e9`.

## Decisions

| # | Item | Decision | Rationale |
| --- | --- | --- | --- |
| 2 | Bootstrap health-detector template rule `network-policy-total-isolation` (`operational_memory.py:1805-1945`, rule `:1934`, Go test `:2051`). It matched SREGym's injected deny-all exactly: podSelector matchLabels, `policyTypes [Ingress, Egress]`, empty rules. | **(a) remove** | The objective never mentions network policy, and the hotel source has none. The rule dates from SDO's first commit, next to an e2e config that listed exactly these problems. The judge inherited it: round 1 of the workspace judge edits the template. The template now checks only the objective's own clauses: required Deployments available, and selector-backed Services with ready endpoints. Reachability faults still surface through the objective's "representative requests succeed" clause (the judge's synthetic traffic) and the always-on `service-endpoints` detector. The `workload_label_sets` and `network_policy_names` plan fields fed only this rule, so they were deleted. The lifecycle-reuse check now compares each Deployment's and Service's declared relationships directly, which was the one other thing those label sets did. We did not generalize the rule. A generic "policy isolates a required workload" check would still be in the template only because of S3. The judge may still write such a check if it decides the topology needs one. |
| 3 | Health-judge prompt: "emit a stable missing-ConfigMap finding", "ConfigMaps created imperatively at deployment time …" (`agents.py:405-409`), and the workspace judge's "Derive required ConfigMaps …" (`:486-488`). | **(a) remove**, replaced by a fault-agnostic method | Both describe how SREGym creates and deletes the S1 and K1 targets. Both prompts now say: choose checks from the objective and the application's topology, "not from a catalogue of fault types". They also say to derive each check's dependencies from objects observed at detection time rather than from hard-coded names, and to write matching and near-miss tests for every check. |
| 3b | Found during the fix. `_validate_health_judge_artifact` rejected every global-objective detector that did not derive ConfigMap references from pod templates and read `ConfigMaps()` (`operational_memory.py:712-738`). `sdo detector check` ran the same test. | **(a) remove** | The validator forced an S1-shaped check into every accepted detector. It now checks only the judge's contract: digest, ownership, coverage provenance, namespace handling, oracles and registration. It never requires a check for a particular fault class. Regression test: `test_global_health_objective_does_not_mandate_a_fault_specific_check`. |
| 4 | Adapter `goal.md` clause "required non-optional ConfigMap volume references remain present" (`driver.py:317-330`). It was inlined into every responder prompt. | **(a) remove** | The clause names S1's fault class. The objective now says: named Deployments remain available, named Services expose ready endpoints (ExternalName Services excepted), and representative requests succeed. It states outcomes and names only the observed topology. The objective's text changed, and so did its digest, so no earlier lifecycle can be reused with it. |
| 5 | Judge Spec contract forced watches on NetworkPolicy, Endpoints and EndpointSlice (`agents.py:379-384`, `_canonicalize_health_registration` at `operational_memory.py:1490-1505`, bootstrap manifest). The coverage text also asked for "every … ConfigMap and NetworkPolicy" (`agents.py:395-400`). | **(b) generalize** | The watch list is now a single constant, `HEALTH_OBJECTIVE_WATCHES`. It holds every Kubernetes kind the SDK's `DetectionContext` exposes except high-churn Events: Pod, ConfigMap, Service, Deployment, ReplicaSet (new), NetworkPolicy, Endpoints, EndpointSlice. It is rendered into the prompt, the canonical Spec, the bootstrap template and the manifest. `_ensure_health_objective_watches` keeps an existing manifest in sync. The rule follows the SDK, not a fault. The coverage paragraph no longer lists kinds for the judge to think about. It says the controller canonicalizes coverage from the trusted inventory, and that coverage is provenance metadata that chooses no check. The kind set stays in `COVERED_RESOURCE_KINDS`, as the adapter's observed configuration kinds (see Disclosure). |
| 6 | Warm path: "After restoring a missing ConfigMap or Secret …, delete the stuck pods or rollout-restart …" (`codex.py:427-429`). | **(a) remove** | This is an S1 repair tip. On the warm path, the learned playbook owns the repair. It was learned from an independently verified outcome, so any restart step it needs is written into it. |
| 7 | Reflection prompt: the same mount-backoff tip (`reflection.py:201-203`). Its permission text said "delete NetworkPolicies" (`:183`). The responder RBAC granted NetworkPolicy get/list/delete and read-only Services (`rbac.yaml:82-84`). | Tip **(a) remove**. RBAC and permission text **(b) generalize** | The tip steered playbook content toward S1. The RBAC was shaped around the benchmark's repairs. A responder could delete a NetworkPolicy but could not edit a Service, so the only way to "fix" S2 inside the Role was to relabel pods to match the broken selector. The `sdo-responder` Role is now generic namespace-scoped edit on application resources, following Kubernetes' built-in `edit` role: get/list/watch/create/update/patch/delete on configmaps, services, pods, persistentvolumeclaims, deployments, statefulsets, daemonsets, replicasets, networkpolicies, ingresses and jobs, plus read access to pod logs, events, endpoints and endpointslices. It deliberately leaves out Secrets (values must never reach a model), RBAC objects, and pods/exec, attach and port-forward (already forbidden in playbooks). With this Role, S2 is repaired by patching the Service selector back to the source manifest. The permission text in the reflection prompt now states that Role, and `test_reflection_states_which_kubectl_verbs_the_responder_may_use` checks it against `rbac.yaml`. |
| 8 | State-diff text: objects not listed "however suspicious their names or contents look … did not cause this incident" (`codex.py:479-482`). | **(b) reword, keep the inference** | The inference is sound and generic: objects unchanged since the healthy baseline did not cause a new incident on their own. The "suspicious names" steer was written against the decoys and was removed. |
| 9 | Adapter responder instructions: "require each affected Service to expose a ready endpoint for the current rollout" (`runtime.py:141-157`). | **(b) reword** | Now "verify the affected user-facing requests succeed". The rollout-agreement check and `sdo incident status` stay; both are generic. |
| 10 | The lifecycle deployer, and the non-workspace judge, ran with `cwd` = the hosting workspace (`agents.py:527`). In SREGym that path sits under a stage directory named after the problem (for example `r1-s1-missing-configmap`). | **(b) neutral path** | `run_initial_lifecycle` clones the repository into `<tmp>/sdo-lifecycle-source-*/application` and runs every read-only lifecycle session there. The workspace judge already used `<tmp>/…/application`. Regression test: `test_lifecycle_agents_never_run_in_a_directory_named_after_the_benchmark_stage`. |
| 11 | `TRAFFIC_AUTHORING` example used the hotel API (`/hotels`, `inDate`/`outDate`, 2015 dates) (`agents.py:148-149`), and the audit docstring used `/hotels`. | **(b) generic example** | The example is now `GET /items` with `from`/`to` date parameters. It still shows how to use `DateRange`/`DaysAfter`. |
| 19 | Fastloop (dev only): Codex ran with `cwd` and `CODEX_HOME` under `<results>/<index>_<problem_id>/` (`fastloop/codex_agent.py:207-240`). | **(b) neutral path** | Codex now runs in a per-incident `<tmp>/codex-incident-*/workdir` with a fresh `codex_home` beside it. Logs and the prompt still go to the problem-named results directory, which Codex does not see. |
| — | `docs/architecture.md` said the state diff hides "a benchmark's decoy ConfigMaps". | **reword** | This is a doc, not a prompt, but the wording framed a production mechanism around a decoy. |

## Regression guard

`tests/unit/test_benchmark_neutrality.py` scans the text that can reach an agent, a template or a detector:
`sdo/`, `controller/{sdk,core,runtime,builder}` (excluding `_test.go`), `benchmarks/sregym/adapter/` and
`libs/sdo_core`. It fails if any of these tokens comes back:

- `network-policy-total-isolation` and `deny-all`
- "missing-ConfigMap finding"
- `failure-admin`
- `mongo-geo-script` and `mongo-rate-script`
- the SREGym problem ids
- "however suspicious"
- "kubelet mount backoff"
- "delete NetworkPolicies"
- the hotel paths `/hotels`, `inDate`, `outDate` and `/recommendations`

It also renders the adapter goal and responder instructions and checks they state outcomes, not fault
classes. Add a token only when it names a specific benchmark fault, decoy or application.

## Consequence: a new seed

Seed `30e023d` (the lifecycle commit used for luna reuse, the no-LLM suite and the phase-1 configs) was
built with these hints:

- its `goal.md` carries the ConfigMap clause;
- its health detector carries `network-policy-total-isolation`;
- its judge was prompted and validated toward a missing-ConfigMap check.

Phase 1 must seed from a fresh lifecycle run on this code. The four SDO phase-1 configs now set
`[pipeline] workspace_seed = "PENDING-FRESH-LIFECYCLE-SEED"`. The runner refuses to launch stage 0 until
that value is replaced by the new seed's directory. The seed-generation steps are in
`benchmarks/sregym/experiments/assurance/phase1/RUNBOOK.md` ("Seed"). Results from `30e023d`-seeded runs,
such as the luna-reuse SDO arm and RC1/RC2 fastloop timings, are pre-fairness measurements.

The in-repo no-LLM-suite seed `benchmarks/sregym/experiments/assurance/seeds/hotel_reservation_30e023d`
still contains the old rule. The suite uses a scripted responder and makes no paper claim about SDO's
detection. Replace it with the new seed once that seed exists.

## Disclosure

These items are kept. A reader might see them as tailored, so each is listed here with the reason it stays.

- **The always-on `service-endpoints` health detector** fires when a selector-backed Service has no ready
  endpoints. It detects S2 and K2 directly. We keep it because "every Service that selects pods has a ready
  endpoint" is a basic Kubernetes health invariant that any operator ships, and the objective's own
  "Services expose ready endpoints" clause states the same thing. It was added in `7a18568`, the day before
  the phase-1 plan, while SDO was already being developed against these SREGym problems.
- **The objective's "Services expose ready endpoints" clause.** The adapter writes the objective in place of
  a human. The clause lists the observed Services and asks for ready endpoints, which is generic
  availability wording. It does not name selectors or probes.
- **`sdk.ConfigMapReferencesForDeployment` in the detector SDK and its prompt reference.** It is a generic
  SDK helper that lists a pod template's ConfigMap references. It is the only reference helper the SDK
  has; there is none for Secrets or PVCs. That makes ConfigMap references easier to check than other
  references. It stays as a public SDK API, and no prompt tells the judge to use it.
- **The adapter's active topology adds non-optional ConfigMap volume references** as active ConfigMaps
  (`driver.py`). This is coverage provenance: an object the running workloads reference is part of the
  active topology. It selects no check.
- **`COVERED_RESOURCE_KINDS` = ConfigMap, Deployment, NetworkPolicy, Service.** These are the configuration
  kinds the adapter lists (`kubectl get deployments,services,configmaps,networkpolicies`). NetworkPolicy is
  on the list although the hotel source has none. Coverage is controller-canonicalized metadata and does
  not reach a check.
- **The health-objective watches include NetworkPolicy and ConfigMap.** They are part of the rule "every
  snapshot kind except Events" and are not chosen per fault.
- **The state-diff kinds** (Services, workloads, NetworkPolicies, ConfigMaps, Secrets, Roles and
  RoleBindings) are the application configuration kinds any change tracker covers. The diff reports every
  change in them; it does not single out a fault.
- **The responder Role's generic edit covers NetworkPolicies and Services**, so it can repair S2 and S3.
  That follows from the Role mirroring Kubernetes' `edit` role, not from those faults.
