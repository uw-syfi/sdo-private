# network_policy_block masking check (D25 mechanism)

No LLM, no Codex. One throwaway kind cluster (Calico), hotel-reservation deployed from source by
`python -m benchmarks.sregym.fastloop up` (the no-LLM assurance suite's deploy path), plain `curl` probes.
Raw data: `np_masking_evidence/` (CSV time series, conntrack and ss dumps, driver scripts).

## Verdict: confirmed

The `deny-all-recommendation` NetworkPolicy blocks every new connection to and from `recommendation`,
but the frontend's already-open gRPC connection (frontend :59524 -> recommendation :8085) stays ESTABLISHED
and keeps carrying requests. Users see 100% success and unchanged latency for the whole 10-minute window.
The fault only becomes user-visible when the frontend redials (here: the frontend pod was deleted), and then
`/recommendations` fails 100% while every other endpoint keeps working.

One refinement to D25: recommendation's own egress to mongodb-recommendation does not matter for serving
while the fault is latent, because requests are served without touching Mongo (step 6). It matters only when
recommendation restarts, which crash-loops.

## Setup

- Cluster `np-verify0` (`fastloop up` names it `<prefix><worker-id>`), kind + Calico, deployed with
  `fastloop up --cluster-prefix np-verify --worker-id 0 --builder np-verify-builder`.
- Side finding: the cluster came up with 1 control plane + 3 workers, not 1+1. `--kind-worker-nodes` did
  not shrink it (the base kind config has 3 workers). It does not affect this result.
- The `wrk2-job` that `up` starts was deleted first, so the only traffic is the probes below.
- frontend and recommendation landed on the same node (`np-verify0-worker3`), so conntrack for both is in
  one place. The helper pod ran on `np-verify0-worker`.
- Fault: the exact object from `third_party/sregym/sregym/conductor/problems/network_policy_block.py`
  (`np_masking_evidence/netpol.yaml`): `podSelector io.kompose.service=recommendation`, policyTypes
  Ingress+Egress, empty ingress and egress.
- Probes (`probe.sh`, alpine helper pod, a fresh `curl` process per request, ~2 req/s each, 5 s timeout):
  - rec: `GET frontend:5000/recommendations?require=dis&lat=37.7749&lon=-122.4194` (the fixture's `recommend`
    scenario in `generators.go`)
  - ctl: `GET frontend:5000/hotels?inDate=2015-04-09&outDate=2015-04-10&lat=..&lon=..` (search: geo, rate,
    profile; no recommendation)
  - usr (phase 2 only): `GET /user?username=Cornell_1&password=1111111111` (login, user service)

## Steps 2-3: baseline and 10 minutes of fault (`ts_phase1.csv`)

Policy applied at t=0 (`markers.txt`). All requests are HTTP 200, curl exit 0.

| phase | endpoint | requests | HTTP 200 | failed | p50 ms | p95 ms | max ms |
|---|---|---|---|---|---|---|---|
| baseline, 60 s | rec | 133 | 133 | 0 | 2.2 | 2.4 | 3.0 |
| baseline, 60 s | ctl | 132 | 132 | 0 | 4.1 | 4.7 | 6.2 |
| fault 0-300 s | rec | 655 | 655 | 0 | 2.1 | 2.5 | 3.6 |
| fault 0-300 s | ctl | 653 | 653 | 0 | 4.0 | 4.6 | 5.6 |
| fault 300-600 s | rec | 666 | 666 | 0 | 2.1 | 2.4 | 3.2 |
| fault 300-600 s | ctl | 663 | 663 | 0 | 4.0 | 4.6 | 6.2 |

Per minute since injection (minutes -1 to 9): rec 130-132 of 130-132 OK at 2.1-2.2 ms p50 in every minute.
No step, ramp, or blip at injection.

### Connection evidence

`conntrack -L` on the node, filtered to frontend -> recommendation:8085, sampled every 30 s from before the
policy until 600 s after (`conntrack.txt`, 22 samples). The same single entry is present in every sample:

```
## baseline_0s
tcp 6 86399 ESTABLISHED src=192.168.59.135 dst=192.168.59.138 sport=59524 dport=8085 src=192.168.59.138 dst=192.168.59.135 sport=8085 dport=59524 [ASSURED] mark=0 use=1
## fault_0s
tcp 6 86399 ESTABLISHED src=192.168.59.135 dst=192.168.59.138 sport=59524 dport=8085 ... [ASSURED]
## fault_600s
tcp 6 86399 ESTABLISHED src=192.168.59.135 dst=192.168.59.138 sport=59524 dport=8085 ... [ASSURED]
```

`ss -tnoi` inside the frontend pod's netns (`nsenter`; `ss_mid.txt` ~75 s into the fault, `ss_end.txt` at
600 s) shows the socket ESTABLISHED and still moving data:

```
0 0 192.168.59.135:59524 192.168.59.138:8085 timer:(keepalive,...)
   bytes_sent:203478 segs_out:5351 data_segs_out:4281 lastsnd:52     (+75 s)
   bytes_sent:366066 segs_out:11111 data_segs_out:7736 lastsnd:76    (+605 s)
```

The recommendation side (`[::ffff:192.168.59.138]:8085`) mirrors it (data_segs_in 4278 -> 7733). About 3,455
more data segments crossed a flow the policy says is denied. Calico allows the tracked flow and evaluates
policy only on new flows (the first SYN).

## Step 4: fresh dial is blocked (`fresh_dial.txt`, +46 s into the fault)

From the helper pod (a new netns), `nc -z -w 3`:

| target | result |
|---|---|
| recommendation pod IP :8085 | timeout after 3.00 s, rc=1 |
| recommendation ClusterIP 10.96.100.9:8085 | timeout after 3.00 s, rc=1 |
| frontend:5000 (control) | rc=0 |
| user:8086 (control) | rc=0 |

The policy is enforced; only new flows are affected.

## Step 5: frontend pod deleted (`ts_phase2.csv`, t=0 is the delete)

Three probes at ~2 req/s, bucketed per 10 s (ok/total):

| t (s) | rec | ctl | usr |
|---|---|---|---|
| -20 .. -10 | all 200, 2 ms | all 200 | all 200 |
| 0 (pod turnover) | 0/11, HTTP 500 | 20/22 | 20/22 |
| 10 .. 120 | 0/~22 per bucket, HTTP 500 | ~22/22, 4 ms | ~22/22, 1 ms |

- rec body: `rpc error: code = Unavailable desc = there is no connection available`, HTTP 500, ~1 ms. It fails
  fast; it is not a timeout.
- ctl and usr each had 2-3 refused connections (curl exit 7) in the ~1 s between the old pod dying and the
  new one becoming Ready (a Service endpoints gap, not policy). After that they are 100% OK.
- Conntrack for the new frontend pod (192.168.7.80) shows only `SYN_SENT ... [UNREPLIED]` entries to
  192.168.59.138:8085, retried by gRPC, never ESTABLISHED.
- The new frontend started and served normally (`frontend_new_logs.txt`); its gRPC dial is lazy, so it does
  not fail readiness.

## Step 6: recommendation's egress to mongodb-recommendation

- recommendation holds two pooled connections to mongodb-recommendation (10.96.151.47:27017), ESTABLISHED in
  conntrack throughout the fault; both survive the policy.
- They are not on the request path. Across ~1,300 recommendation requests during the fault, one connection's
  counters did not move (`bytes_sent` 35285, `segs_out` 238 at +75 s and +605 s) and the other grew by only
  1,890 bytes (segs_out 24 -> 59) over 530 s, which is driver heartbeat traffic. Recommendations are served
  from the process's in-memory data, loaded from Mongo at startup.
- Restarting recommendation exposes the fault on that side too: the new pod is also under the policy and
  `initializeDatabase` (`cmd/recommendation/main.go:39` -> `db.go:22`) panics because it cannot reach Mongo
  (exit code 2). At 75 s it had restarted 3 times (`rec_restart.txt`).

So there are two independent latent-to-visible triggers: a frontend redial (frontend restart, an error that
resets the channel, connection age or idle limit) and a recommendation restart. Neither occurred in 10
minutes of steady operation.

## Takeaways

- **Meaning.** `network_policy_block` on a link with warm long-lived gRPC channels is a latent fault: the
  object exists and is enforced, but no user request fails until a connection is re-established. D25's
  mechanism (the frontend's existing connection to recommendation is the masked one, not the prober's) is
  correct, and this is direct evidence (conntrack, ss, latency series), not inference. Confidence: high for
  this stack (kind, Calico, hotel-reservation with lazy long-lived gRPC); single run, but the mechanism is
  deterministic and the counters are unambiguous. It should generalise to any CNI that allows established
  flows, and not to one that tears down flows on policy change.
- **Not "no impact" in the strict sense.** Zero current user impact, but the system is one pod restart,
  connection recycle or scale-out away from a hard outage (recommendation 500s while other paths work, and
  the recommendation pod itself cannot start). Any new consumer of recommendation (new frontend replica, HPA
  scale-out) is broken on arrival, and an operator restarting the frontend triggers the outage.
- **Implication for SDO detection.** A user-facing probe, even with a fresh connection per request (D25),
  cannot see this fault: the fresh connection lands on the frontend, which reuses its old connection. The
  signal is a per-service-link reachability probe making a fresh TCP dial from a separate pod to each
  dependency's Service and pod port (step 4: timed out in 3.0 s while all HTTP probes stayed green). The
  edges can come from the traffic catalog's `DependsOn`. It should be deterministic Go in `controller/sdk`
  like other detectors, at the traffic detector's cadence (one handshake per edge). A NetworkPolicy
  state-change detector is a cheap complement (the baseline diff sees the new policy at t=0). Node-level
  conntrack is not available to an in-cluster controller, so do not depend on it.
- **How the paper should describe it.** As a latent (masked) fault, not a fault SDO or the baseline "missed":
  its user-visible manifestation is delayed until a connection re-establishes, so time-to-detect by symptom
  is undefined or unbounded here. Report it separately from symptomatic faults, or report detect-by-symptom
  and detect-by-link-probe separately, and state the mechanism (established flows survive NetworkPolicy
  under Calico). Present the never-fired traffic detector as evidence for the need for dependency-link
  probes, not as a detector failure, and note that runs of this fault depend on whether the frontend happens
  to redial.
- **Next step.** Add the per-edge fresh-dial probe to the detector SDK, then rerun `network_policy_block`
  with and without a frontend restart to confirm time-to-detect drops from never to the probe interval.
