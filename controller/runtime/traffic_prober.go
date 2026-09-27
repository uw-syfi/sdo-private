package runtime

import (
	"context"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"time"

	"sigs.k8s.io/yaml"

	"sdo.dev/controller/sdk"
	"sdo.dev/controller/sdk/traffic"
)

// TrafficMixDirectory is where the health judge stores traffic mixes,
// relative to the application repository root.
const TrafficMixDirectory = ".sdo/diagnostics/traffic"

const (
	defaultProbeInFlight = 16
	defaultProbeCapacity = 64
)

// TrafficMixNames returns the mixes the detectors consume, sorted.
func TrafficMixNames(detectors []sdk.Detector) []string {
	seen := make(map[string]bool)
	for _, detector := range detectors {
		consumer, ok := detector.(traffic.Consumer)
		if !ok {
			continue
		}
		for _, name := range consumer.TrafficMixes() {
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

// LoadTrafficMixes loads each named mix from <appRoot>/.sdo/diagnostics/traffic/<name>.yaml.
// A mix that cannot be loaded is reported by name instead of stopping the
// controller, so its detector can surface the failure as a detector error.
func LoadTrafficMixes(appRoot string, names []string) ([]traffic.Mix, map[string]string) {
	mixes := make([]traffic.Mix, 0, len(names))
	failures := make(map[string]string)
	for _, name := range names {
		path := filepath.Join(appRoot, TrafficMixDirectory, name+".yaml")
		payload, err := os.ReadFile(path)
		if err != nil {
			failures[name] = err.Error()
			continue
		}
		document, err := yaml.YAMLToJSON(payload)
		if err != nil {
			failures[name] = fmt.Sprintf("parse %s: %v", path, err)
			continue
		}
		mix, err := traffic.ParseMixJSON(document)
		if err != nil {
			failures[name] = fmt.Sprintf("%s: %v", path, err)
			continue
		}
		if mix.Name != name {
			failures[name] = fmt.Sprintf("%s: mix name %q must match its file name", path, mix.Name)
			continue
		}
		mixes = append(mixes, mix)
	}
	return mixes, failures
}

// NewSyntheticTraffic builds the prober for the mixes the detectors consume,
// loaded from the application repository at appRoot. It returns a nil prober
// when synthetic traffic is disabled or no detector consumes a mix, and the
// mixes that could not be loaded by name.
func NewSyntheticTraffic(
	appRoot string,
	namespace string,
	detectors []sdk.Detector,
	enabled bool,
	notify func(),
) (*TrafficProber, map[string]string, error) {
	names := TrafficMixNames(detectors)
	if !enabled || len(names) == 0 {
		return nil, map[string]string{}, nil
	}
	mixes, failures := LoadTrafficMixes(appRoot, names)
	prober, err := NewTrafficProber(TrafficProberConfig{
		Namespace: namespace, Mixes: mixes, Notify: notify,
		Client: &http.Client{Transport: &http.Transport{
			// Every probe opens a fresh connection so a Service that lost its
			// endpoints cannot hide behind a kept-alive connection to an old pod.
			DisableKeepAlives: true,
		}},
	})
	if err != nil {
		return nil, nil, err
	}
	return prober, failures, nil
}

// ServiceBaseURL addresses a target Service through cluster DNS.
func ServiceBaseURL(target traffic.Target, namespace string) string {
	return fmt.Sprintf("%s://%s.%s.svc:%d", target.Scheme, target.Service, namespace, target.Port)
}

type TrafficProberConfig struct {
	Namespace string
	Mixes     []traffic.Mix
	Client    traffic.Doer
	// BaseURL resolves a target; ServiceBaseURL when nil.
	BaseURL func(traffic.Target, string) string
	// Notify wakes the controller when a probe result may change a verdict.
	Notify      func()
	MaxInFlight int
	// Capacity bounds the samples kept per route.
	Capacity int
}

type routeState struct {
	route     traffic.Route
	slo       traffic.SLO
	current   int
	samples   []traffic.Sample
	qualified bool
}

type mixState struct {
	mix    traffic.Mix
	routes []*routeState
	total  int
}

// TrafficProber sends each mix's synthetic requests at its configured rate,
// spreading them over routes by smooth weighted round robin, and keeps the
// recent samples detectors judge.
type TrafficProber struct {
	config   TrafficProberConfig
	mu       sync.Mutex
	mixes    map[string]*mixState
	order    []string
	inFlight chan struct{}
	cancel   context.CancelFunc
	wait     sync.WaitGroup
}

func NewTrafficProber(config TrafficProberConfig) (*TrafficProber, error) {
	if config.Namespace == "" {
		return nil, fmt.Errorf("traffic prober namespace is required")
	}
	if config.Client == nil {
		config.Client = &http.Client{}
	}
	if config.BaseURL == nil {
		config.BaseURL = ServiceBaseURL
	}
	if config.MaxInFlight <= 0 {
		config.MaxInFlight = defaultProbeInFlight
	}
	if config.Capacity <= 0 {
		config.Capacity = defaultProbeCapacity
	}
	prober := &TrafficProber{
		config: config, mixes: make(map[string]*mixState), inFlight: make(chan struct{}, config.MaxInFlight),
	}
	for _, mix := range config.Mixes {
		if err := mix.Validate(); err != nil {
			return nil, err
		}
		if _, duplicate := prober.mixes[mix.Name]; duplicate {
			return nil, fmt.Errorf("duplicate traffic mix %q", mix.Name)
		}
		state := &mixState{mix: mix}
		for _, route := range mix.Routes {
			state.routes = append(state.routes, &routeState{route: route, slo: mix.RouteSLO(route)})
			state.total += route.Weight
		}
		prober.mixes[mix.Name] = state
		prober.order = append(prober.order, mix.Name)
	}
	sort.Strings(prober.order)
	return prober, nil
}

// Start begins sending traffic; calling it on a running prober is a no-op.
func (p *TrafficProber) Start(ctx context.Context) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.cancel != nil {
		return
	}
	running, cancel := context.WithCancel(ctx)
	p.cancel = cancel
	for _, name := range p.order {
		interval := time.Duration(float64(time.Second) / p.mixes[name].mix.RatePerSecond)
		p.wait.Add(1)
		go p.run(running, name, interval)
	}
}

// Stop ends traffic and waits for in-flight requests to finish.
func (p *TrafficProber) Stop() {
	p.mu.Lock()
	cancel := p.cancel
	p.cancel = nil
	p.mu.Unlock()
	if cancel != nil {
		cancel()
	}
	p.wait.Wait()
}

// Reset forgets every sample and qualification, for example after the
// application was redeployed during a maintenance window.
func (p *TrafficProber) Reset() {
	p.mu.Lock()
	defer p.mu.Unlock()
	for _, state := range p.mixes {
		for _, route := range state.routes {
			route.samples = nil
			route.qualified = false
			route.current = 0
		}
	}
}

func (p *TrafficProber) run(ctx context.Context, name string, interval time.Duration) {
	defer p.wait.Done()
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
		select {
		case p.inFlight <- struct{}{}:
		default:
			// Every slot waits on a slow route; skipping keeps the offered
			// load bounded instead of queueing a burst behind it.
			continue
		}
		route, base := p.next(name)
		p.wait.Add(1)
		go func() {
			defer p.wait.Done()
			defer func() { <-p.inFlight }()
			p.execute(ctx, name, route, base)
		}()
	}
}

// ProbeNext synchronously sends the mix's next request and records it.
func (p *TrafficProber) ProbeNext(ctx context.Context, name string) {
	route, base := p.next(name)
	p.execute(ctx, name, route, base)
}

func (p *TrafficProber) next(name string) (traffic.Route, string) {
	p.mu.Lock()
	defer p.mu.Unlock()
	state := p.mixes[name]
	var selected *routeState
	for _, route := range state.routes {
		route.current += route.route.Weight
		if selected == nil || route.current > selected.current {
			selected = route
		}
	}
	selected.current -= state.total
	return selected.route, p.config.BaseURL(state.mix.Target, p.config.Namespace)
}

func (p *TrafficProber) execute(ctx context.Context, name string, route traffic.Route, base string) {
	sample := traffic.Execute(ctx, p.config.Client, base, route, route.Timeout.Duration())
	if ctx.Err() != nil && sample.Outcome != traffic.OutcomeOK {
		// Cancellation is the controller stopping, not the application failing.
		return
	}
	if sample.Outcome == traffic.OutcomeOK && route.Cleanup != nil {
		traffic.Execute(ctx, p.config.Client, base, traffic.Route{ID: route.ID, Request: *route.Cleanup}, route.Timeout.Duration())
	}
	if p.record(name, route.ID, sample) && p.config.Notify != nil {
		p.config.Notify()
	}
}

// record stores a sample and reports whether it may change the route's
// verdict: it failed, or a failure is still inside the route's SLO window.
func (p *TrafficProber) record(name string, routeID string, sample traffic.Sample) bool {
	p.mu.Lock()
	defer p.mu.Unlock()
	state := p.mixes[name]
	for _, route := range state.routes {
		if route.route.ID != routeID {
			continue
		}
		route.samples = append(route.samples, sample)
		if len(route.samples) > p.config.Capacity {
			route.samples = append([]traffic.Sample(nil), route.samples[len(route.samples)-p.config.Capacity:]...)
		}
		if sample.Outcome == traffic.OutcomeOK {
			route.qualified = true
		}
		recent := route.samples
		if len(recent) > route.slo.Window {
			recent = recent[len(recent)-route.slo.Window:]
		}
		for _, candidate := range recent {
			if candidate.Outcome != traffic.OutcomeOK {
				return true
			}
		}
		return false
	}
	return false
}

// Windows returns every mix's current observations.
func (p *TrafficProber) Windows() map[string]traffic.Window {
	p.mu.Lock()
	defer p.mu.Unlock()
	now := time.Now().UTC()
	windows := make(map[string]traffic.Window, len(p.mixes))
	for name, state := range p.mixes {
		routes := make([]traffic.RouteObservations, 0, len(state.routes))
		for _, route := range state.routes {
			routes = append(routes, traffic.RouteObservations{
				RouteID: route.route.ID, Samples: append([]traffic.Sample(nil), route.samples...),
				Qualified: route.qualified,
			})
		}
		windows[name] = traffic.Window{Mix: state.mix, ObservedAt: now, Routes: routes}
	}
	return windows
}

// WaitWarm waits until every route of every mix has at least one sample, so
// the first evaluation after start or resume judges observed traffic rather
// than an empty window. It reports whether that happened within timeout.
func (p *TrafficProber) WaitWarm(ctx context.Context, timeout time.Duration) bool {
	deadline := time.NewTimer(timeout)
	defer deadline.Stop()
	ticker := time.NewTicker(50 * time.Millisecond)
	defer ticker.Stop()
	for {
		if p.warm() {
			return true
		}
		select {
		case <-ctx.Done():
			return false
		case <-deadline.C:
			return p.warm()
		case <-ticker.C:
		}
	}
}

func (p *TrafficProber) warm() bool {
	p.mu.Lock()
	defer p.mu.Unlock()
	for _, state := range p.mixes {
		for _, route := range state.routes {
			if len(route.samples) == 0 {
				return false
			}
		}
	}
	return true
}

// Summary describes each route's warm-up state for the controller log.
func (p *TrafficProber) Summary() map[string]map[string]any {
	summary := make(map[string]map[string]any)
	for name, window := range p.Windows() {
		routes := make(map[string]any, len(window.Routes))
		for _, route := range window.Routes {
			routes[route.RouteID] = map[string]any{"samples": len(route.Samples), "qualified": route.Qualified}
		}
		summary[name] = map[string]any{
			"target": ServiceBaseURL(window.Mix.Target, p.config.Namespace), "rate_per_second": window.Mix.RatePerSecond,
			"routes": routes,
		}
	}
	return summary
}

// TrafficSnapshotProvider adds synthetic-traffic windows to another
// provider's snapshots.
type TrafficSnapshotProvider struct {
	Base     SnapshotProvider
	Prober   *TrafficProber
	Failures map[string]string
}

func (p TrafficSnapshotProvider) Snapshot(ctx context.Context) (sdk.DetectionContext, error) {
	base, err := p.Base.Snapshot(ctx)
	if err != nil {
		return nil, err
	}
	windows := make(map[string]traffic.Window)
	if p.Prober != nil {
		windows = p.Prober.Windows()
	}
	for name, failure := range p.Failures {
		windows[name] = traffic.Window{LoadError: failure}
	}
	return trafficContext{DetectionContext: base, windows: windows}, nil
}

type trafficContext struct {
	sdk.DetectionContext
	windows map[string]traffic.Window
}

func (c trafficContext) TrafficWindow(mix string) (traffic.Window, bool) {
	window, ok := c.windows[mix]
	return window, ok
}
