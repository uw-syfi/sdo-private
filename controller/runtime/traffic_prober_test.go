package runtime

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"sdo.dev/controller/sdk"
	"sdo.dev/controller/sdk/sdktest"
	"sdo.dev/controller/sdk/traffic"
)

const proberMixYAML = `apiVersion: sdo.dev/v1alpha1
kind: TrafficMix
name: frontend
target:
  service: frontend
  port: 5000
ratePerSecond: 20
routes:
  - id: search
    method: GET
    path: /hotels
    weight: 2
  - id: login
    method: GET
    path: /user
    expect:
      bodyContains: Login successfully!
  - id: reserve
    method: GET
    path: /reservation
    mutates: true
    dataPolicy: idempotent
    syntheticMarker: sdo-synthetic
    query:
      customerName: sdo-synthetic
      number: "0"
`

type fakeFrontend struct {
	mu      sync.Mutex
	broken  bool
	paths   []string
	headers []string
}

func (f *fakeFrontend) setBroken(broken bool) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.broken = broken
}

func (f *fakeFrontend) ServeHTTP(writer http.ResponseWriter, request *http.Request) {
	f.mu.Lock()
	f.paths = append(f.paths, request.URL.Path)
	f.headers = append(f.headers, request.Header.Get(traffic.SyntheticHeader))
	broken := f.broken
	f.mu.Unlock()
	if broken {
		http.Error(writer, "rpc error: code = Unavailable desc = connection error", http.StatusInternalServerError)
		return
	}
	if request.URL.Path == "/user" {
		_, _ = io.WriteString(writer, `{"message":"Login successfully!"}`)
		return
	}
	_, _ = io.WriteString(writer, "{}")
}

func (f *fakeFrontend) requestedPaths() []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]string(nil), f.paths...)
}

func writeMix(t *testing.T, root string, name string, text string) string {
	t.Helper()
	dir := filepath.Join(root, ".sdo", "diagnostics", "traffic")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	if err := os.WriteFile(filepath.Join(dir, name+".yaml"), []byte(text), 0o644); err != nil {
		t.Fatalf("write mix: %v", err)
	}
	return root
}

func loadedMix(t *testing.T) traffic.Mix {
	t.Helper()
	root := writeMix(t, t.TempDir(), "frontend", proberMixYAML)
	mixes, failures := LoadTrafficMixes(root, []string{"frontend"})
	if len(failures) != 0 || len(mixes) != 1 {
		t.Fatalf("load mixes: %v %v", mixes, failures)
	}
	return mixes[0]
}

func newTestProber(t *testing.T, server *httptest.Server, notify func()) *TrafficProber {
	t.Helper()
	prober, err := NewTrafficProber(TrafficProberConfig{
		Namespace: "hotel-reservation", Mixes: []traffic.Mix{loadedMix(t)}, Client: server.Client(),
		BaseURL: func(traffic.Target, string) string { return server.URL }, Notify: notify,
	})
	if err != nil {
		t.Fatalf("new prober: %v", err)
	}
	return prober
}

// The Python commit-time gate (sdo/operational_memory/models.py TrafficMix)
// accepts this fixture; the Go executor must accept the same document.
func TestRuntimeAcceptsTheSharedHotelFixtureMix(t *testing.T) {
	payload, err := os.ReadFile(filepath.Join("..", "..", "tests", "fixtures", "hotel_reservation", "traffic", "frontend.yaml"))
	if err != nil {
		t.Fatalf("read fixture: %v", err)
	}
	root := writeMix(t, t.TempDir(), "frontend", string(payload))
	mixes, failures := LoadTrafficMixes(root, []string{"frontend"})
	if len(failures) != 0 || len(mixes) != 1 || len(mixes[0].Routes) != 4 || !mixes[0].Routes[3].Mutates {
		t.Fatalf("fixture must load in Go as in Python: %+v %v", mixes, failures)
	}
}

func TestLoadTrafficMixesReadsOnlyReferencedYAMLAndReportsFailures(t *testing.T) {
	root := writeMix(t, t.TempDir(), "frontend", proberMixYAML)
	writeMix(t, root, "renamed", strings.Replace(proberMixYAML, "name: frontend", "name: other", 1))
	writeMix(t, root, "unused", "not: [valid")
	mixes, failures := LoadTrafficMixes(root, []string{"frontend", "renamed", "missing"})
	if len(mixes) != 1 || mixes[0].Name != "frontend" || mixes[0].Routes[0].Weight != 2 {
		t.Fatalf("expected the frontend mix only, got %+v", mixes)
	}
	if !strings.Contains(failures["renamed"], "file name") || !strings.Contains(failures["missing"], "no such file") {
		t.Fatalf("expected per-mix load failures, got %v", failures)
	}
	if _, reported := failures["unused"]; reported {
		t.Fatalf("mixes no detector consumes must not be loaded, got %v", failures)
	}
}

func trafficDetector(id string, mix string) sdk.Detector {
	return traffic.NewDetector(sdk.DetectorSpec{
		ID: id, Class: sdk.DetectorClassHealth, Owner: sdk.DetectorOwnerHealthJudge,
		Watches: []sdk.WatchKind{traffic.Watch}, Interval: 10 * time.Second,
		Persistence: sdk.PersistencePolicy{Firing: 2, Clearing: 2},
		Batching:    sdk.BatchingPolicy{Severity: sdk.SeverityCritical}, OriginatingCommit: "lifecycle-bootstrap",
	}, mix)
}

func TestNewSyntheticTrafficProbesOnlyConsumedMixes(t *testing.T) {
	root := writeMix(t, t.TempDir(), "frontend", proberMixYAML)
	detectors := []sdk.Detector{trafficDetector("traffic-frontend", "frontend"), trafficDetector("traffic-admin", "admin")}
	prober, failures, err := NewSyntheticTraffic(root, "hotel-reservation", detectors, true, nil)
	if err != nil || prober == nil {
		t.Fatalf("expected a prober, got %v %v", prober, err)
	}
	if _, ok := prober.Windows()["frontend"]; !ok || len(failures) != 1 || failures["admin"] == "" {
		t.Fatalf("expected the frontend mix probed and the admin mix reported missing, got %v %v", prober.Windows(), failures)
	}
	disabled, _, err := NewSyntheticTraffic(root, "hotel-reservation", detectors, false, nil)
	if err != nil || disabled != nil {
		t.Fatalf("disabled synthetic traffic must not create a prober, got %v %v", disabled, err)
	}
	none, noneFailures, err := NewSyntheticTraffic(root, "hotel-reservation", nil, true, nil)
	if err != nil || none != nil || len(noneFailures) != 0 {
		t.Fatalf("without a consuming detector no traffic is sent, got %v %v %v", none, noneFailures, err)
	}
}

func TestProberSpreadsRequestsByWeightDeterministically(t *testing.T) {
	frontend := &fakeFrontend{}
	server := httptest.NewServer(frontend)
	defer server.Close()
	prober := newTestProber(t, server, nil)
	for index := 0; index < 8; index++ {
		prober.ProbeNext(context.Background(), "frontend")
	}
	got := strings.Join(frontend.requestedPaths(), ",")
	want := "/hotels,/user,/reservation,/hotels,/hotels,/user,/reservation,/hotels"
	if got != want {
		t.Fatalf("smooth weighted round robin order: got %s want %s", got, want)
	}
	for _, header := range frontend.headers {
		if header == "" {
			t.Fatalf("every synthetic request must carry %s", traffic.SyntheticHeader)
		}
	}
}

func TestProberWindowsQualifyRoutesAndNotifyOnlyWhileFailing(t *testing.T) {
	frontend := &fakeFrontend{}
	server := httptest.NewServer(frontend)
	defer server.Close()
	var notifications atomic.Int32
	prober := newTestProber(t, server, func() { notifications.Add(1) })
	for index := 0; index < 4; index++ {
		prober.ProbeNext(context.Background(), "frontend")
	}
	if notifications.Load() != 0 {
		t.Fatalf("healthy probes must not wake the controller, got %d notifications", notifications.Load())
	}
	window := prober.Windows()["frontend"]
	for _, route := range window.Routes {
		if !route.Qualified || len(route.Samples) == 0 {
			t.Fatalf("a route that succeeded must be qualified with samples, got %+v", route)
		}
	}
	frontend.setBroken(true)
	prober.ProbeNext(context.Background(), "frontend")
	if notifications.Load() != 1 {
		t.Fatalf("a failed probe must wake the controller, got %d", notifications.Load())
	}
	frontend.setBroken(false)
	for index := 0; index < 4; index++ {
		prober.ProbeNext(context.Background(), "frontend")
	}
	if notifications.Load() < 2 {
		t.Fatalf("recovering probes must keep waking the controller until the failure ages out, got %d", notifications.Load())
	}
}

func TestProberResetForgetsObservations(t *testing.T) {
	frontend := &fakeFrontend{}
	server := httptest.NewServer(frontend)
	defer server.Close()
	prober := newTestProber(t, server, nil)
	prober.ProbeNext(context.Background(), "frontend")
	prober.Reset()
	for _, route := range prober.Windows()["frontend"].Routes {
		if route.Qualified || len(route.Samples) != 0 {
			t.Fatalf("reset must drop samples and qualification, got %+v", route)
		}
	}
}

func TestProberWarmsEveryRouteBeforeReportingReady(t *testing.T) {
	frontend := &fakeFrontend{}
	server := httptest.NewServer(frontend)
	defer server.Close()
	prober := newTestProber(t, server, nil)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	prober.Start(ctx)
	defer prober.Stop()
	if !prober.WaitWarm(ctx, 5*time.Second) {
		t.Fatalf("prober did not observe every route within the warm-up bound: %+v", prober.Windows())
	}
	for _, route := range prober.Windows()["frontend"].Routes {
		if len(route.Samples) == 0 {
			t.Fatalf("warm prober must have sampled route %s", route.RouteID)
		}
	}
}

func TestTrafficSnapshotsCarryWindowsAndLoadFailures(t *testing.T) {
	frontend := &fakeFrontend{}
	server := httptest.NewServer(frontend)
	defer server.Close()
	prober := newTestProber(t, server, nil)
	provider := TrafficSnapshotProvider{
		Base:     staticProvider{snapshot: sdktest.Snapshot{NamespaceName: "hotel-reservation"}},
		Prober:   prober,
		Failures: map[string]string{"checkout": "open checkout.yaml: no such file"},
	}
	snapshot, err := provider.Snapshot(context.Background())
	if err != nil {
		t.Fatalf("snapshot: %v", err)
	}
	if _, ok := traffic.WindowFrom(snapshot, "frontend"); !ok {
		t.Fatalf("snapshot must carry the frontend window")
	}
	failed, ok := traffic.WindowFrom(snapshot, "checkout")
	if !ok || failed.LoadError == "" {
		t.Fatalf("snapshot must carry the load failure, got %+v", failed)
	}
	if snapshot.Namespace() != "hotel-reservation" {
		t.Fatalf("snapshot must keep the base context, got %q", snapshot.Namespace())
	}
}

// The synthetic-traffic detector opens an incident when the served routes
// fail and the incident's evidence names each failing route.
func TestControllerOpensIncidentFromFailingSyntheticTraffic(t *testing.T) {
	frontend := &fakeFrontend{}
	server := httptest.NewServer(frontend)
	defer server.Close()
	prober := newTestProber(t, server, nil)
	detector := traffic.NewDetector(sdk.DetectorSpec{
		ID: "traffic-frontend", Class: sdk.DetectorClassHealth, Owner: sdk.DetectorOwnerHealthJudge,
		Watches: []sdk.WatchKind{traffic.Watch}, Interval: 10 * time.Second,
		Persistence:       sdk.PersistencePolicy{Firing: 2, Clearing: 2},
		Batching:          sdk.BatchingPolicy{Severity: sdk.SeverityCritical},
		OriginatingCommit: "lifecycle-bootstrap",
	}, "frontend")
	dispatcher := &recordingDispatcher{requests: make(chan IncidentRequest, 1)}
	controller, err := NewController(ControllerConfig{
		Application: "hotel", Namespace: "hotel-reservation", SourceCommit: "source", DeployedCommit: "deployed",
		ArchitectureSummaryPath: ".sdo/arch.md", HealthObjectivePath: ".sdo/goal.md", RepositoryWorktree: "/tmp/hotel",
		ResponseTimeout: time.Minute, FiringThreshold: 2, ClearThreshold: 2,
	}, []sdk.Detector{detector}, TrafficSnapshotProvider{
		Base: staticProvider{snapshot: sdktest.Snapshot{NamespaceName: "hotel-reservation"}}, Prober: prober,
	}, dispatcher, time.Unix(0, 0))
	if err != nil {
		t.Fatalf("new controller: %v", err)
	}
	for index := 0; index < 4; index++ {
		prober.ProbeNext(context.Background(), "frontend")
	}
	now := time.Unix(0, 0)
	if err := controller.StepEvents(context.Background(), now, []sdk.WatchKind{traffic.Watch}); err != nil {
		t.Fatalf("healthy step: %v", err)
	}
	if controller.IncidentOpen() {
		t.Fatalf("healthy traffic must not open an incident")
	}
	frontend.setBroken(true)
	for index := 0; index < 12; index++ {
		prober.ProbeNext(context.Background(), "frontend")
	}
	for step := 1; step <= 3; step++ {
		if err := controller.StepEvents(context.Background(), now.Add(time.Duration(step)*time.Second), []sdk.WatchKind{traffic.Watch}); err != nil {
			t.Fatalf("failing step: %v", err)
		}
	}
	executePendingEffect(t, controller)
	request := awaitRequest(t, dispatcher.requests)
	rules := make([]string, 0, len(request.Findings))
	for _, finding := range request.Findings {
		rules = append(rules, finding.RuleID)
		if !strings.Contains(finding.Evidence, "rpc error") {
			t.Fatalf("incident finding must carry the route's error body, got %q", finding.Evidence)
		}
	}
	if strings.Join(rules, ",") != "route-slo.login,route-slo.reserve,route-slo.search" {
		t.Fatalf("incident must name every failing route, got %v", rules)
	}
}
