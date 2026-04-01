# Prompt Optimization Log — Crucible Agent v2

## Baseline: Run `20260331_184135_crucible`

**Config**: `sregym_agents/experiments/default.toml`
- Model: `gemini-2.5-flash`
- Agent: `crucible` with `prompt_version = "v2"`
- KB: Seeded from prior run `20260331_012034_crucible`
- LTM retrieval: enabled (search_prior_incidents tool)
- Judge: disabled
- Max iterations: 3 diagnosis, 3 mitigation

**Results** (48 problems):

| Metric | Value |
|---|---|
| Diagnosis accuracy | 56.2% (27/48) |
| Mitigation accuracy | 77.1% (37/48) |
| Both correct | 45.8% (22/48) |

### Per-Problem-Type Breakdown

| Problem Type | N | Diag % | Mit % |
|---|---|---|---|
| readiness_probe_misconfiguration | 3 | 100% | 100% |
| service_port_conflict | 2 | 100% | 100% |
| sidecar_port_conflict | 3 | 100% | 67% |
| wrong_service_selector | 3 | 100% | 100% |
| stale_coredns_config | 4 | 75% | 75% |
| liveness_probe_misconfiguration | 3 | 67% | 100% |
| missing_configmap | 3 | 67% | 67% |
| rolling_update_misconfigured | 3 | 67% | 33% |
| service_dns_resolution_failure | 3 | 67% | 100% |
| duplicate_pvc_mounts | 3 | 33% | 67% |
| liveness_probe_too_aggressive | 3 | 33% | 100% |
| missing_env_variable | 3 | 33% | 33% |
| missing_service | 3 | 33% | 67% |
| wrong_dns_policy | 3 | 33% | 100% |
| incorrect_port_assignment | 3 | 0% | 67% |
| k8s_target_port_misconfig | 3 | 0% | 67% |

---

## Root Cause Analysis of Failures

### Failure Pattern 1: Port-related faults (0% diagnosis)

Both `incorrect_port_assignment` (env var points to wrong port) and `k8s_target_port_misconfig` (Service targetPort doesn't match containerPort) scored 0% on diagnosis across all 6 instances.

**What goes wrong**: The agent sees connection errors in dependent services and investigates those services' configs or logs, rather than checking port alignment between Service specs and pod containerPorts, or between env var addresses and actual listening ports.

**Example**: `incorrect_port_assignment` — checkout has `CURRENCY_ADDR=currency:3000` but currency listens on 8080. Agent investigated OTEL exporter config on currency instead.

**Example**: `k8s_target_port_misconfig` — post-storage-service targetPort=9999 but pod listens on 9090. Agent investigated nginx-thrift localhost config instead.

### Failure Pattern 2: Missing-things detection (33% diagnosis)

`missing_env_variable` scored 33%. The agent checks existing env vars for correctness but never checks whether expected vars are absent.

**Example**: PRODUCT_CATALOG_ADDR deleted from frontend. Agent found FRONTEND_ADDR=:8080 and blamed that instead.

### Failure Pattern 3: Symptom vs. K8s config confusion (33% diagnosis)

`liveness_probe_too_aggressive` scored 33%. Agent sees CrashLoopBackOff, investigates application code/logs for crash cause, but the actual cause is the probe kills the pod before it starts.

**Example**: Agent found "Python syntax error" in ConfigMap instead of checking that livenessProbe has initialDelaySeconds=0, periodSeconds=1, failureThreshold=1.

### Failure Pattern 4: Noise distraction

The agent consistently fixates on the loudest visible anomaly rather than tracing the causal chain. OTEL/observability issues, nginx-thrift localhost config, and OpenSearch memory warnings are common distractors.

### Failure Pattern 5: KB retrieval as confirmation bias

search_prior_incidents often returns a match that confirms the agent's initial (wrong) hypothesis, reinforcing the error rather than challenging it.

---

## Optimization Round 1

**Date**: 2026-03-31

### Hypothesis

The agent fails on port-related, missing-variable, and probe-aggression faults because:
1. The triage tool doesn't explicitly check port alignment or env var completeness
2. The diagnosis prompt doesn't provide enough guidance on these specific fault classes
3. The agent's "investigate upstream" principle isn't operationalized with concrete checks

### Changes

#### Change 1: Enhance triage_cluster.j2 — Add port & env var checks

**Rationale**: Triage is the first investigative step. If it surfaces port mismatches, missing env vars, and probe timing issues as anomalies, the diagnosis agent has better signal to work with.

**Additions to REQUIRED CHECKS**:
- Port alignment: compare every Service's targetPort against its backing pods' containerPort
- Env var address validation: for env vars containing service addresses (host:port), verify the target port matches the target service's actual listening port
- Probe timing: flag livenessProbes with initialDelaySeconds < 5 AND (periodSeconds < 3 OR failureThreshold < 3) as potentially too aggressive for slow-starting containers

#### Change 2: Strengthen diagnosis_agent_system.j2 — Add anti-patterns and concrete checks

**Rationale**: The reasoning principles are too abstract. Adding concrete anti-patterns (don't blame OTEL/telemetry, check for missing things, always verify port alignment) should reduce the specific failure modes observed.

**Additions to REASONING PRINCIPLES**:
- "Check for absent resources": explicitly check if expected env vars, Services, or ConfigMaps are missing, not just if present ones are misconfigured
- "Port alignment is a top-priority check": when seeing connection refused / can't connect errors, immediately check targetPort vs containerPort and env var addresses vs actual ports
- "Probe timing before application bugs": when pods are in CrashLoopBackOff, check probe configuration (especially initialDelaySeconds, periodSeconds, failureThreshold) before investigating application code

#### Change 3: Enhance search_prior_incidents.j2 — Add counter-hypothesis instruction

**Rationale**: KB retrieval currently confirms first hypotheses. Adding an instruction to also return "what else to check if none match" and to challenge the top match should reduce confirmation bias.

### Expected Impact

- `incorrect_port_assignment`: 0% → 50-100% (triage now checks port alignment)
- `k8s_target_port_misconfig`: 0% → 50-100% (triage now checks targetPort vs containerPort)
- `missing_env_variable`: 33% → 67-100% (explicit absent-resource checking)
- `liveness_probe_too_aggressive`: 33% → 67-100% (probe timing check in triage + diagnosis)
- Overall diagnosis: 56% → 70%+ target

---

## Optimization Round 1 — Results

Run: `20260331_213345_crucible` (10 problems, vs 48 in baseline — high variance)

| Metric | Baseline (N=48) | Round 1 (N=10) | Delta |
|---|---|---|---|
| Diagnosis accuracy | 56.2% | 50.0% | -6.2 pp |
| Mitigation accuracy | 77.1% | 100.0% | +22.9 pp |
| Both correct | 45.8% | 50.0% | +4.2 pp |

### Per-Problem-Type Results (Round 1)

| Problem Type | Baseline Diag % | Round 1 Diag | Round 1 Mit |
|---|---|---|---|
| missing_env_variable | 33% | PASS | PASS |
| missing_service | 33% | PASS | PASS |
| readiness_probe_misconfiguration | 100% | PASS | PASS |
| service_dns_resolution_failure | 67% | PASS | PASS |
| sidecar_port_conflict | 100% | PASS | PASS |
| incorrect_port_assignment | 0% | FAIL | PASS |
| k8s_target_port_misconfig | 0% | FAIL | PASS |
| missing_configmap | 67% | FAIL | PASS |
| rolling_update_misconfigured | 67% | FAIL | PASS |
| wrong_dns_policy | 33% | FAIL | PASS |

### Trajectory Analysis

**What worked:**
- `missing_env_variable` improved (33% → PASS): The diagnosis agent explicitly used the
  "Missing resources" TOP-PRIORITY CHECK to connect `"Could not parse target name """` to a
  deleted env var. The triage sub-agent did NOT find it — the diagnosis agent applied the
  reasoning itself.

**What didn't work — Failure Mode 1: Triage skips systematic checks**
- `incorrect_port_assignment`: Triage dumped deployment YAMLs but never cross-referenced env
  var addresses against service ports. The script-based port alignment check was not executed.
- `k8s_target_port_misconfig`: Triage saved service and deployment YAMLs but never compared
  targetPort vs containerPort. Got stuck on jsonpath quoting issues and gave up.
- The triage sub-agent (gemini-2.5-flash) is not reliably executing the REQUIRED CHECKS when
  they require multi-step scripting.

**What didn't work — Failure Mode 2: Diagnosis ignores confirmed signals**
- `wrong_dns_policy`: Triage correctly found `payment dnsPolicy=None`. LTM verifier confirmed
  it as root cause with a complete causal chain. But the diagnosis agent ignored BOTH signals
  and chased a `flagd` feature flag rabbit hole instead.

---

## Optimization Round 2

**Date**: 2026-03-31

### Hypothesis

Two independent failure modes require two targeted fixes:

1. **Triage sub-agent skips complex checks**: The gemini-2.5-flash triage agent doesn't
   reliably execute multi-step checks described in prose. Providing concrete executable
   script templates (copy-paste-ready shell commands) will increase compliance.

2. **Diagnosis agent ignores confirmed+corroborated candidates**: When both `search_prior_incidents`
   confirms a candidate AND the triage report contains the same anomaly, the agent should
   trust these signals instead of investigating alternative symptoms.

### Changes

#### Change 1: Diagnosis — Trust confirmed+corroborated candidates

**File**: `diagnosis_agent_system.j2`

Strengthened the workflow step 3 instruction for when exactly ONE confirmed candidate exists:
- Old: "validate it makes sense and proceed to step 6"
- New: "AND the triage report corroborates the anomaly it describes, trust it — do a quick
  sanity check (1-2 commands) and proceed to step 6. Do NOT abandon a confirmed+corroborated
  candidate to chase a different symptom."

#### Change 2: Diagnosis — Deployment strategy check on stuck rollouts

**File**: `diagnosis_agent_system.j2`

Added to TOP-PRIORITY CHECKS: when pods are stuck in Init/Pending during a rollout, check
`spec.strategy.rollingUpdate.maxUnavailable`. If 100%, that's the root cause — it terminates
all existing pods before replacements are ready.

#### Change 3: Coverage checker — Evaluate against user-facing symptoms, not all anomalies

**File**: `check_hypothesis_coverage.j2`

Reframed the evaluation criteria. Previously required the hypothesis to explain ALL triage
anomalies. Now:
- First identify USER-FACING SYMPTOMS (load generator failures, frontend errors, etc.)
- Hypothesis must explain those end-to-end
- Independent anomalies that don't contribute to user-visible failures (e.g., port mismatch
  on an unrelated service, monitoring misconfigs) don't cause rejection
- Only reject if a user-facing symptom remains unexplained

**Rationale**: In the `missing_configmap` case, the correct diagnosis (missing media-mongodb
ConfigMap) was rejected 3 times because it couldn't explain unrelated anomalies like
`server_name localhost` and a media-frontend port mismatch. These are real issues but not
the injected fault. The injected fault always causes user-visible breakage, so that's the
right evaluation bar.

### Expected Impact

- `wrong_dns_policy`: 0% → 100% (agent should trust confirmed candidate from triage+LTM)
- `rolling_update_misconfigured`: 0% → 50-100% (agent now checks deployment strategy)
- `missing_configmap`: 0% → 50-100% (coverage checker won't reject over unrelated anomalies)
- Overall diagnosis: 50% → 70-80%

---

## Optimization Round 2 — Results

*Pending: run experiment and fill in results*
