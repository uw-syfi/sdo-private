package runtime

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"sort"
	"strings"
	"sync"
	"time"

	"sdo.dev/controller/runtime/prober"
	"sdo.dev/controller/sdk"
	"sdo.dev/controller/sdk/traffic"
)

// TrafficWorkloadNames returns the workloads the detectors consume, sorted.
func TrafficWorkloadNames(detectors []sdk.Detector) []string {
	seen := make(map[string]bool)
	for _, detector := range detectors {
		consumer, ok := detector.(traffic.Consumer)
		if !ok {
			continue
		}
		for _, name := range consumer.TrafficWorkloads() {
			seen[name] = true
		}
	}
	names := make([]string, 0, len(seen))
	for name := range seen {
		names = append(names, name)
	}
	sort.Strings(names)
	return names
}

// ProberAPI is the controller's view of the isolated prober.
type ProberAPI interface {
	Windows(ctx context.Context) (map[string]traffic.Window, error)
	Burst(ctx context.Context, request prober.BurstRequest) (prober.BurstResult, error)
	Reset(ctx context.Context) error
}

// HTTPProberClient calls a prober's HTTP API.
type HTTPProberClient struct {
	// BaseURL resolves the prober's address, for example the pod IP. After a
	// transport error the client asks again with refresh set, so a replaced
	// or deleted prober pod is found or recreated.
	BaseURL func(ctx context.Context, refresh bool) (string, error)
	Client  *http.Client
}

// StaticProberURL addresses a prober at a fixed URL.
func StaticProberURL(url string) func(context.Context, bool) (string, error) {
	return func(context.Context, bool) (string, error) { return url, nil }
}

func (c HTTPProberClient) do(ctx context.Context, method string, path string, body any, into any) error {
	response, err := c.send(ctx, method, path, body, false)
	if err != nil && ctx.Err() == nil {
		response, err = c.send(ctx, method, path, body, true)
	}
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode >= 300 {
		message, _ := io.ReadAll(io.LimitReader(response.Body, 4096))
		return fmt.Errorf("prober %s %s: HTTP %d: %s", method, path, response.StatusCode, strings.TrimSpace(string(message)))
	}
	if into == nil {
		return nil
	}
	return json.NewDecoder(io.LimitReader(response.Body, 32<<20)).Decode(into)
}

func (c HTTPProberClient) send(ctx context.Context, method string, path string, body any, refresh bool) (*http.Response, error) {
	base, err := c.BaseURL(ctx, refresh)
	if err != nil {
		return nil, fmt.Errorf("locate prober: %w", err)
	}
	var payload io.Reader
	if body != nil {
		encoded, err := json.Marshal(body)
		if err != nil {
			return nil, err
		}
		payload = bytes.NewReader(encoded)
	}
	request, err := http.NewRequestWithContext(ctx, method, strings.TrimSuffix(base, "/")+path, payload)
	if err != nil {
		return nil, err
	}
	request.Header.Set("Content-Type", "application/json")
	client := c.Client
	if client == nil {
		client = &http.Client{Timeout: 90 * time.Second}
	}
	response, err := client.Do(request)
	if err != nil {
		return nil, fmt.Errorf("prober %s %s: %w", method, path, err)
	}
	return response, nil
}

func (c HTTPProberClient) Windows(ctx context.Context) (map[string]traffic.Window, error) {
	var response prober.WindowsResponse
	if err := c.do(ctx, http.MethodGet, prober.PathWindows, nil, &response); err != nil {
		return nil, err
	}
	return response.Windows, nil
}

func (c HTTPProberClient) Burst(ctx context.Context, request prober.BurstRequest) (prober.BurstResult, error) {
	var result prober.BurstResult
	err := c.do(ctx, http.MethodPost, prober.PathBursts, request, &result)
	return result, err
}

func (c HTTPProberClient) Reset(ctx context.Context) error {
	return c.do(ctx, http.MethodPost, prober.PathReset, map[string]string{}, nil)
}

// DefaultTrafficPollInterval is how often the controller reads the prober.
const DefaultTrafficPollInterval = 500 * time.Millisecond

// TrafficObserver keeps the latest prober observations of the workloads the
// detectors consume and wakes the controller while any of them holds a
// failed iteration, so detection and clearing do not wait for detector
// intervals. A healthy application causes no extra evaluations.
type TrafficObserver struct {
	api       ProberAPI
	workloads []string
	notify    func()
	interval  time.Duration

	mu      sync.Mutex
	windows map[string]traffic.Window
	failure string
	cancel  context.CancelFunc
	wait    sync.WaitGroup
}

// NewTrafficObserver returns nil when no detector consumes a workload.
func NewTrafficObserver(api ProberAPI, workloads []string, notify func(), interval time.Duration) *TrafficObserver {
	if api == nil || len(workloads) == 0 {
		return nil
	}
	if interval <= 0 {
		interval = DefaultTrafficPollInterval
	}
	return &TrafficObserver{api: api, workloads: append([]string(nil), workloads...), notify: notify, interval: interval}
}

// Poll reads the prober once and reports whether any consumed workload holds
// a failed iteration within its SLO window.
func (o *TrafficObserver) Poll(ctx context.Context) bool {
	windows, err := o.api.Windows(ctx)
	o.mu.Lock()
	defer o.mu.Unlock()
	if err != nil {
		if ctx.Err() == nil {
			o.failure = err.Error()
		}
		return false
	}
	o.failure = ""
	o.windows = windows
	for _, name := range o.workloads {
		window, ok := windows[name]
		if !ok {
			continue
		}
		if windowHasRecentFailure(window) {
			return true
		}
	}
	return false
}

func windowHasRecentFailure(window traffic.Window) bool {
	for _, observed := range window.Scenarios {
		samples := observed.Samples
		size := window.Workload.ScenarioSLO(observed.Scenario.ID).Window
		if len(samples) > size {
			samples = samples[len(samples)-size:]
		}
		for _, sample := range samples {
			if sample.Outcome == traffic.OutcomeError || sample.Outcome == traffic.OutcomeTimeout {
				return true
			}
		}
	}
	return false
}

// Start polls until Stop; calling it on a running observer is a no-op.
func (o *TrafficObserver) Start(ctx context.Context) {
	o.mu.Lock()
	if o.cancel != nil {
		o.mu.Unlock()
		return
	}
	running, cancel := context.WithCancel(ctx)
	o.cancel = cancel
	o.mu.Unlock()
	o.wait.Add(1)
	go func() {
		defer o.wait.Done()
		ticker := time.NewTicker(o.interval)
		defer ticker.Stop()
		for {
			if o.Poll(running) && o.notify != nil {
				o.notify()
			}
			select {
			case <-running.Done():
				return
			case <-ticker.C:
			}
		}
	}()
}

// Stop ends polling and waits for the poller.
func (o *TrafficObserver) Stop() {
	o.mu.Lock()
	cancel := o.cancel
	o.cancel = nil
	o.mu.Unlock()
	if cancel != nil {
		cancel()
	}
	o.wait.Wait()
}

// Reset asks the prober to forget its observations and drops the local copy.
func (o *TrafficObserver) Reset(ctx context.Context) error {
	o.mu.Lock()
	o.windows = nil
	o.mu.Unlock()
	return o.api.Reset(ctx)
}

// WaitWarm polls until every scenario of every consumed workload has a
// sample, so the next evaluation, which may be the all-clear that precedes a
// fault, judges observed traffic. It reports whether that happened in time.
func (o *TrafficObserver) WaitWarm(ctx context.Context, timeout time.Duration) bool {
	deadline := time.NewTimer(timeout)
	defer deadline.Stop()
	ticker := time.NewTicker(100 * time.Millisecond)
	defer ticker.Stop()
	for {
		o.Poll(ctx)
		if o.warm() {
			return true
		}
		select {
		case <-ctx.Done():
			return false
		case <-deadline.C:
			return o.warm()
		case <-ticker.C:
		}
	}
}

func (o *TrafficObserver) warm() bool {
	o.mu.Lock()
	defer o.mu.Unlock()
	for _, name := range o.workloads {
		window, ok := o.windows[name]
		if !ok || len(window.Scenarios) == 0 {
			return false
		}
		for _, observed := range window.Scenarios {
			if len(observed.Samples) == 0 {
				return false
			}
		}
	}
	return true
}

// Windows returns the consumed workloads' windows. When the prober cannot be
// read, or does not run a consumed workload, the window carries a LoadError
// so the detector reports an error: a health verdict that cannot be observed
// must not count as clear.
func (o *TrafficObserver) Windows() map[string]traffic.Window {
	o.mu.Lock()
	defer o.mu.Unlock()
	windows := make(map[string]traffic.Window, len(o.workloads))
	for _, name := range o.workloads {
		window, ok := o.windows[name]
		switch {
		case o.failure != "":
			windows[name] = traffic.Window{LoadError: "prober unavailable: " + o.failure}
		case !ok && o.windows != nil:
			windows[name] = traffic.Window{LoadError: "prober does not run health-probe workload " + name}
		case ok:
			windows[name] = window
		}
	}
	return windows
}

// Summary describes each consumed workload for the controller log.
func (o *TrafficObserver) Summary() map[string]any {
	summary := make(map[string]any)
	for name, window := range o.Windows() {
		if window.LoadError != "" {
			summary[name] = map[string]any{"error": window.LoadError}
			continue
		}
		scenarios := make(map[string]any, len(window.Scenarios))
		for _, observed := range window.Scenarios {
			scenarios[observed.Scenario.ID] = map[string]any{
				"samples": len(observed.Samples), "qualified": observed.Qualified, "invalid": observed.Invalid,
				"target": observed.Scenario.Target.Service, "depends_on": observed.Scenario.DependsOn,
			}
		}
		summary[name] = map[string]any{
			"rate_per_second": window.Workload.RatePerSecond, "seed": window.Workload.Seed, "scenarios": scenarios,
		}
	}
	return summary
}

// TrafficSnapshotProvider adds synthetic-traffic windows to another
// provider's snapshots.
type TrafficSnapshotProvider struct {
	Base     SnapshotProvider
	Observer *TrafficObserver
}

func (p TrafficSnapshotProvider) Snapshot(ctx context.Context) (sdk.DetectionContext, error) {
	base, err := p.Base.Snapshot(ctx)
	if err != nil {
		return nil, err
	}
	windows := map[string]traffic.Window{}
	if p.Observer != nil {
		windows = p.Observer.Windows()
	}
	return trafficContext{DetectionContext: base, windows: windows}, nil
}

type trafficContext struct {
	sdk.DetectionContext
	windows map[string]traffic.Window
}

func (c trafficContext) TrafficWindow(workload string) (traffic.Window, bool) {
	window, ok := c.windows[workload]
	return window, ok
}
