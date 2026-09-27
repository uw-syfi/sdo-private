package traffic_test

import (
	"context"
	"strings"
	"testing"
	"time"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

	"sdo.dev/controller/sdk"
	"sdo.dev/controller/sdk/sdktest"
	"sdo.dev/controller/sdk/traffic"
)

var observedAt = time.Date(2026, 9, 27, 12, 0, 0, 0, time.UTC)

func detectorSpec() sdk.DetectorSpec {
	return sdk.DetectorSpec{
		ID: "traffic-frontend", Class: sdk.DetectorClassHealth, Owner: sdk.DetectorOwnerHealthJudge,
		Watches: []sdk.WatchKind{traffic.Watch}, Interval: 10 * time.Second,
		Persistence: sdk.PersistencePolicy{Firing: 2, Clearing: 2},
		Batching:    sdk.BatchingPolicy{Severity: sdk.SeverityCritical, Debounce: 500 * time.Millisecond},
		Playbooks:   []string{".sdo/playbooks/health-objective/README.md"}, OriginatingCommit: "lifecycle-bootstrap",
	}
}

func frontendMix(t *testing.T) traffic.Mix {
	t.Helper()
	mix, err := traffic.ParseMixJSON([]byte(`{
	  "apiVersion": "sdo.dev/v1alpha1", "kind": "TrafficMix", "name": "frontend",
	  "target": {"service": "frontend", "port": 5000},
	  "routes": [
	    {"id": "search", "method": "GET", "path": "/hotels"},
	    {"id": "login", "method": "GET", "path": "/user", "expect": {"bodyContains": "Login successfully!"}},
	    {"id": "reserve", "method": "GET", "path": "/reservation", "mutates": true, "dataPolicy": "idempotent",
	     "syntheticMarker": "sdo-synthetic", "query": {"customerName": "sdo-synthetic", "number": "0"}}
	  ]
	}`))
	if err != nil {
		t.Fatalf("parse mix: %v", err)
	}
	return mix
}

func ok(offset time.Duration) traffic.Sample {
	return traffic.Sample{At: observedAt.Add(-offset), Latency: 3 * time.Millisecond, Status: 200, Outcome: traffic.OutcomeOK}
}

func failed(offset time.Duration, status int, message string) traffic.Sample {
	return traffic.Sample{
		At: observedAt.Add(-offset), Latency: 2 * time.Millisecond, Status: status, Outcome: traffic.OutcomeError,
		Error: message,
	}
}

func timedOut(offset time.Duration) traffic.Sample {
	return traffic.Sample{At: observedAt.Add(-offset), Latency: 2 * time.Second, Outcome: traffic.OutcomeTimeout,
		Error: "context deadline exceeded"}
}

func samples(values ...traffic.Sample) []traffic.Sample { return values }

func healthyRoute(id string) traffic.RouteObservations {
	return traffic.RouteObservations{RouteID: id, Qualified: true, Samples: samples(
		ok(5*time.Second), ok(4*time.Second), ok(3*time.Second), ok(2*time.Second), ok(time.Second),
	)}
}

// hotelSnapshot is the healthy hotel-reservation namespace with SREGym's
// never-used "revoke admin" decoy ConfigMaps present.
func hotelSnapshot(windows map[string]traffic.Window) sdktest.Snapshot {
	decoy := func(name string) corev1.ConfigMap {
		return corev1.ConfigMap{
			ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: "hotel-reservation"},
			Data:       map[string]string{"revoke-admin-mongo.sh": "mongo admin --eval 'db.revokeRolesFromUser(\"admin\", [\"readWrite\"])'"},
		}
	}
	return sdktest.Snapshot{
		NamespaceName: "hotel-reservation",
		ConfigMapList: []corev1.ConfigMap{decoy("failure-admin-geo"), decoy("failure-admin-rate")},
		Traffic:       windows,
	}
}

func window(mix traffic.Mix, routes ...traffic.RouteObservations) map[string]traffic.Window {
	return map[string]traffic.Window{mix.Name: {Mix: mix, ObservedAt: observedAt, Routes: routes}}
}

func TestDetectorDeclaresItsTrafficMix(t *testing.T) {
	detector := traffic.NewDetector(detectorSpec(), "frontend")
	consumer, ok := detector.(traffic.Consumer)
	if !ok || len(consumer.TrafficMixes()) != 1 || consumer.TrafficMixes()[0] != "frontend" {
		t.Fatalf("detector must declare the mix it evaluates, got %#v", detector)
	}
	if detector.Spec().ID != "traffic-frontend" {
		t.Fatalf("detector must keep the registered spec, got %+v", detector.Spec())
	}
}

func TestDetectorIsSilentWithoutProbeObservations(t *testing.T) {
	findings, err := traffic.NewDetector(detectorSpec(), "frontend").Detect(context.Background(), hotelSnapshot(nil))
	if err != nil || len(findings) != 0 {
		t.Fatalf("no observations must mean no judgement, got %v %v", findings, err)
	}
}

func TestDetectorDoesNotFireOnHealthyTrafficWithDecoysPresent(t *testing.T) {
	mix := frontendMix(t)
	snapshot := hotelSnapshot(window(mix, healthyRoute("search"), healthyRoute("login"), healthyRoute("reserve")))
	findings, err := traffic.NewDetector(detectorSpec(), "frontend").Detect(context.Background(), snapshot)
	if err != nil || len(findings) != 0 {
		t.Fatalf("healthy routes must not fire even with decoy ConfigMaps present, got %v %v", findings, err)
	}
}

func TestDetectorToleratesASingleBlip(t *testing.T) {
	mix := frontendMix(t)
	blip := traffic.RouteObservations{RouteID: "search", Qualified: true, Samples: samples(
		ok(5*time.Second), ok(4*time.Second), failed(3*time.Second, 503, "upstream reset"), ok(2*time.Second), ok(time.Second),
	)}
	snapshot := hotelSnapshot(window(mix, blip, healthyRoute("login"), healthyRoute("reserve")))
	findings, err := traffic.NewDetector(detectorSpec(), "frontend").Detect(context.Background(), snapshot)
	if err != nil || len(findings) != 0 {
		t.Fatalf("one failed probe in five must not violate the default SLO, got %v %v", findings, err)
	}
}

// A Service whose selector matches no pod refuses every connection: each
// route of the mix fails and each is named with its evidence.
func TestDetectorNamesEveryFailingRouteForAWrongServiceSelector(t *testing.T) {
	mix := frontendMix(t)
	refused := func(id string) traffic.RouteObservations {
		return traffic.RouteObservations{RouteID: id, Qualified: true, Samples: samples(
			ok(6*time.Second), ok(5*time.Second),
			failed(3*time.Second, 0, "dial tcp 10.96.10.10:5000: connect: connection refused"),
			failed(2*time.Second, 0, "dial tcp 10.96.10.10:5000: connect: connection refused"),
			failed(time.Second, 0, "dial tcp 10.96.10.10:5000: connect: connection refused"),
		)}
	}
	snapshot := hotelSnapshot(window(mix, refused("search"), refused("login"), refused("reserve")))
	findings, err := traffic.NewDetector(detectorSpec(), "frontend").Detect(context.Background(), snapshot)
	if err != nil {
		t.Fatalf("detect: %v", err)
	}
	if len(findings) != 3 {
		t.Fatalf("expected one finding per failing route, got %d: %v", len(findings), findings)
	}
	seen := map[string]bool{}
	for _, finding := range findings {
		seen[finding.RuleID] = true
		if finding.DetectorID != "traffic-frontend" || finding.Status != sdk.FindingActive ||
			finding.Severity != sdk.SeverityCritical {
			t.Fatalf("finding identity: %+v", finding)
		}
		if finding.PrimaryResource != (sdk.ObjectRef{APIVersion: "v1", Kind: "Service", Namespace: "hotel-reservation", Name: "frontend"}) {
			t.Fatalf("finding must point at the mix's target Service, got %+v", finding.PrimaryResource)
		}
		if !strings.Contains(finding.Evidence, "connection refused") || !strings.Contains(finding.Summary, "3/5") {
			t.Fatalf("finding must carry per-route failure evidence, got summary %q evidence %q", finding.Summary, finding.Evidence)
		}
		if len(finding.Playbooks) != 1 {
			t.Fatalf("finding must surface the registered playbooks, got %v", finding.Playbooks)
		}
	}
	for _, rule := range []string{"route-slo.search", "route-slo.login", "route-slo.reserve"} {
		if !seen[rule] {
			t.Fatalf("missing finding %s in %v", rule, seen)
		}
	}
}

func TestDetectorReportsStatusCodesAndErrorBodies(t *testing.T) {
	mix := frontendMix(t)
	broken := traffic.RouteObservations{RouteID: "login", Qualified: true, Samples: samples(
		failed(3*time.Second, 500, "rpc error: code = Unavailable desc = connection error"),
		failed(2*time.Second, 500, "rpc error: code = Unavailable desc = connection error"),
		failed(time.Second, 200, "response body lacks \"Login successfully!\""),
	)}
	snapshot := hotelSnapshot(window(mix, healthyRoute("search"), broken, healthyRoute("reserve")))
	findings, err := traffic.NewDetector(detectorSpec(), "frontend").Detect(context.Background(), snapshot)
	if err != nil || len(findings) != 1 {
		t.Fatalf("expected the login finding only, got %v %v", findings, err)
	}
	finding := findings[0]
	for _, want := range []string{"HTTP 500", "rpc error: code = Unavailable", "GET /user"} {
		if !strings.Contains(finding.Summary+" "+finding.Evidence, want) {
			t.Fatalf("finding lacks %q: summary %q evidence %q", want, finding.Summary, finding.Evidence)
		}
	}
	counts, ok := finding.Metadata["status_counts"].(map[string]int)
	if !ok || counts["500"] != 2 || counts["200"] != 1 {
		t.Fatalf("finding metadata must count status codes, got %#v", finding.Metadata["status_counts"])
	}
}

func TestDetectorFiresOnTimeoutsAndLatency(t *testing.T) {
	mix := frontendMix(t)
	hanging := traffic.RouteObservations{RouteID: "search", Qualified: true, Samples: samples(
		ok(4*time.Second), timedOut(3*time.Second), timedOut(2*time.Second), timedOut(time.Second),
	)}
	slow := traffic.RouteObservations{RouteID: "login", Qualified: true, Samples: samples(
		traffic.Sample{At: observedAt.Add(-3 * time.Second), Latency: 1900 * time.Millisecond, Status: 200, Outcome: traffic.OutcomeOK},
		traffic.Sample{At: observedAt.Add(-2 * time.Second), Latency: 1800 * time.Millisecond, Status: 200, Outcome: traffic.OutcomeOK},
		traffic.Sample{At: observedAt.Add(-time.Second), Latency: 1700 * time.Millisecond, Status: 200, Outcome: traffic.OutcomeOK},
	)}
	snapshot := hotelSnapshot(window(mix, hanging, slow, healthyRoute("reserve")))
	findings, err := traffic.NewDetector(detectorSpec(), "frontend").Detect(context.Background(), snapshot)
	if err != nil || len(findings) != 2 {
		t.Fatalf("expected timeout and latency findings, got %v %v", findings, err)
	}
	joined := findings[0].Summary + findings[1].Summary
	if !strings.Contains(joined, "timeout rate") || !strings.Contains(joined, "p90 latency") {
		t.Fatalf("findings must name the violated SLO dimension, got %q", joined)
	}
}

func TestDetectorIgnoresStaleSamplesAndUnqualifiedRoutes(t *testing.T) {
	mix := frontendMix(t)
	stale := traffic.RouteObservations{RouteID: "search", Qualified: true, Samples: samples(
		failed(5*time.Minute, 503, "old outage"), failed(4*time.Minute, 503, "old outage"), failed(3*time.Minute, 503, "old outage"),
		ok(2*time.Second), ok(time.Second),
	)}
	// A route that never succeeded since observation began is not a
	// qualified health signal: the mix may name a path this deployment
	// does not serve.
	neverWorked := traffic.RouteObservations{RouteID: "login", Qualified: false, Samples: samples(
		failed(3*time.Second, 404, "not found"), failed(2*time.Second, 404, "not found"), failed(time.Second, 404, "not found"),
	)}
	snapshot := hotelSnapshot(window(mix, stale, neverWorked, healthyRoute("reserve")))
	findings, err := traffic.NewDetector(detectorSpec(), "frontend").Detect(context.Background(), snapshot)
	if err != nil || len(findings) != 0 {
		t.Fatalf("stale and unqualified samples must not fire, got %v %v", findings, err)
	}
}

func TestDetectorClearsOnceRecentProbesRecover(t *testing.T) {
	mix := frontendMix(t)
	recovered := traffic.RouteObservations{RouteID: "search", Qualified: true, Samples: samples(
		failed(6*time.Second, 0, "connection refused"), failed(5*time.Second, 0, "connection refused"),
		ok(3*time.Second), ok(2*time.Second), ok(time.Second),
	)}
	snapshot := hotelSnapshot(window(mix, recovered, healthyRoute("login"), healthyRoute("reserve")))
	findings, err := traffic.NewDetector(detectorSpec(), "frontend").Detect(context.Background(), snapshot)
	if err != nil || len(findings) != 0 {
		t.Fatalf("three recovered probes of five must clear the route, got %v %v", findings, err)
	}
}

func TestDetectorReportsAMixTheRuntimeCouldNotLoad(t *testing.T) {
	snapshot := hotelSnapshot(map[string]traffic.Window{"frontend": {LoadError: "traffic mix frontend: invalid"}})
	_, err := traffic.NewDetector(detectorSpec(), "frontend").Detect(context.Background(), snapshot)
	if err == nil || !strings.Contains(err.Error(), "invalid") {
		t.Fatalf("a load error must surface as a detector error, got %v", err)
	}
}
