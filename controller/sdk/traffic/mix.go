// Package traffic is SDO's app-agnostic synthetic-traffic health machinery.
//
// A traffic mix, stored by the health judge in the application's operational
// memory (.sdo/diagnostics/traffic/<name>.yaml), names routes the application
// serves. The controller runtime issues a few requests per second against them
// with Execute and hands the observed samples to detectors as a Window. A
// Detector built with NewDetector judges each route purely from those samples
// against its sliding-window SLO (error rate, timeout rate, latency
// percentile); the controller's firing and clearing persistence then decides
// when that judgement opens or closes an incident. Nothing here knows about a
// particular application, and nothing calls an LLM.
package traffic

import (
	"bytes"
	"encoding/json"
	"fmt"
	"regexp"
	"strings"
	"time"
)

const (
	APIVersion = "sdo.dev/v1alpha1"
	Kind       = "TrafficMix"

	DefaultRatePerSecond     = 4.0
	MaxRatePerSecond         = 20.0
	DefaultTimeout           = 2 * time.Second
	MaxTimeout               = 30 * time.Second
	DefaultWindow            = 5
	DefaultMinSamples        = 3
	DefaultMaxAge            = 30 * time.Second
	DefaultMaxErrorRate      = 0.5
	DefaultMaxTimeoutRate    = 0.5
	DefaultLatencyPercentile = 90
	DefaultMaxLatency        = 1500 * time.Millisecond

	DataPolicyIdempotent   = "idempotent"
	DataPolicySelfCleaning = "self-cleaning"
)

var (
	namePattern    = regexp.MustCompile(`^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$`)
	routeIDPattern = regexp.MustCompile(`^[a-z0-9][a-z0-9_.-]{0,62}$`)
	methods        = map[string]bool{"GET": true, "HEAD": true, "POST": true, "PUT": true, "PATCH": true, "DELETE": true}
)

// Duration is a time.Duration written as a Go duration string ("2s").
type Duration time.Duration

func (d Duration) Duration() time.Duration { return time.Duration(d) }

func (d Duration) MarshalJSON() ([]byte, error) { return json.Marshal(time.Duration(d).String()) }

func (d *Duration) UnmarshalJSON(data []byte) error {
	var text string
	if err := json.Unmarshal(data, &text); err != nil {
		return fmt.Errorf("duration must be a string such as \"2s\": %w", err)
	}
	parsed, err := time.ParseDuration(text)
	if err != nil {
		return err
	}
	*d = Duration(parsed)
	return nil
}

// Target is the Service, in the application namespace, a mix sends traffic to.
type Target struct {
	Service string `json:"service"`
	Port    int    `json:"port"`
	Scheme  string `json:"scheme,omitempty"`
}

// Request is one HTTP request, relative to the mix's target.
type Request struct {
	Method  string            `json:"method"`
	Path    string            `json:"path"`
	Query   map[string]string `json:"query,omitempty"`
	Headers map[string]string `json:"headers,omitempty"`
	Body    string            `json:"body,omitempty"`
}

// Expect describes a successful response. With no Status, any 2xx succeeds.
type Expect struct {
	Status       []int  `json:"status,omitempty"`
	BodyContains string `json:"bodyContains,omitempty"`
}

// SLO bounds a route's recent behaviour. Zero fields inherit the mix's SLO,
// and then the package defaults.
type SLO struct {
	Window            int      `json:"window,omitempty"`
	MinSamples        int      `json:"minSamples,omitempty"`
	MaxAge            Duration `json:"maxAge,omitempty"`
	MaxErrorRate      float64  `json:"maxErrorRate,omitempty"`
	MaxTimeoutRate    float64  `json:"maxTimeoutRate,omitempty"`
	LatencyPercentile int      `json:"latencyPercentile,omitempty"`
	MaxLatency        Duration `json:"maxLatency,omitempty"`
}

// Route is one served user path. A route that changes application state
// (Mutates) must use dedicated synthetic data, named by SyntheticMarker, and
// either be idempotent or carry a Cleanup request.
type Route struct {
	ID string `json:"id"`
	Request
	Weight          int      `json:"weight,omitempty"`
	Timeout         Duration `json:"timeout,omitempty"`
	Expect          Expect   `json:"expect,omitempty"`
	Mutates         bool     `json:"mutates,omitempty"`
	DataPolicy      string   `json:"dataPolicy,omitempty"`
	SyntheticMarker string   `json:"syntheticMarker,omitempty"`
	Cleanup         *Request `json:"cleanup,omitempty"`
	SLO             *SLO     `json:"slo,omitempty"`
}

// Mix is a validated traffic mix. Use ParseMixJSON to obtain one.
type Mix struct {
	APIVersion    string   `json:"apiVersion"`
	Kind          string   `json:"kind"`
	Name          string   `json:"name"`
	Description   string   `json:"description,omitempty"`
	Target        Target   `json:"target"`
	RatePerSecond float64  `json:"ratePerSecond,omitempty"`
	Timeout       Duration `json:"timeout,omitempty"`
	SLO           SLO      `json:"slo,omitempty"`
	Routes        []Route  `json:"routes"`
}

// ParseMixJSON strictly decodes a mix, applies defaults, and validates it.
func ParseMixJSON(data []byte) (Mix, error) {
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	var mix Mix
	if err := decoder.Decode(&mix); err != nil {
		return Mix{}, fmt.Errorf("decode traffic mix: %w", err)
	}
	mix = mix.withDefaults()
	if err := mix.Validate(); err != nil {
		return Mix{}, err
	}
	return mix, nil
}

func (m Mix) withDefaults() Mix {
	if m.Target.Scheme == "" {
		m.Target.Scheme = "http"
	}
	if m.RatePerSecond == 0 {
		m.RatePerSecond = DefaultRatePerSecond
	}
	if m.Timeout == 0 {
		m.Timeout = Duration(DefaultTimeout)
	}
	m.SLO = mergeSLO(defaultSLO(), m.SLO)
	routes := make([]Route, len(m.Routes))
	for index, route := range m.Routes {
		if route.Weight == 0 {
			route.Weight = 1
		}
		if route.Timeout == 0 {
			route.Timeout = m.Timeout
		}
		route.Method = strings.ToUpper(route.Method)
		if route.Cleanup != nil {
			cleanup := *route.Cleanup
			cleanup.Method = strings.ToUpper(cleanup.Method)
			route.Cleanup = &cleanup
		}
		routes[index] = route
	}
	m.Routes = routes
	return m
}

func defaultSLO() SLO {
	return SLO{
		Window: DefaultWindow, MinSamples: DefaultMinSamples, MaxAge: Duration(DefaultMaxAge),
		MaxErrorRate: DefaultMaxErrorRate, MaxTimeoutRate: DefaultMaxTimeoutRate,
		LatencyPercentile: DefaultLatencyPercentile, MaxLatency: Duration(DefaultMaxLatency),
	}
}

func mergeSLO(base SLO, override SLO) SLO {
	if override.Window != 0 {
		base.Window = override.Window
	}
	if override.MinSamples != 0 {
		base.MinSamples = override.MinSamples
	}
	if override.MaxAge != 0 {
		base.MaxAge = override.MaxAge
	}
	if override.MaxErrorRate != 0 {
		base.MaxErrorRate = override.MaxErrorRate
	}
	if override.MaxTimeoutRate != 0 {
		base.MaxTimeoutRate = override.MaxTimeoutRate
	}
	if override.LatencyPercentile != 0 {
		base.LatencyPercentile = override.LatencyPercentile
	}
	if override.MaxLatency != 0 {
		base.MaxLatency = override.MaxLatency
	}
	return base
}

// RouteSLO is the effective SLO of one route of the mix.
func (m Mix) RouteSLO(route Route) SLO {
	slo := mergeSLO(defaultSLO(), m.SLO)
	if route.SLO != nil {
		slo = mergeSLO(slo, *route.SLO)
	}
	return slo
}

// Route returns the mix's route with the given ID.
func (m Mix) Route(id string) (Route, bool) {
	for _, route := range m.Routes {
		if route.ID == id {
			return route, true
		}
	}
	return Route{}, false
}

// Validate reports the first problem that makes the mix unsafe or ambiguous.
func (m Mix) Validate() error {
	if m.APIVersion != APIVersion {
		return fmt.Errorf("traffic mix apiVersion must be %s", APIVersion)
	}
	if m.Kind != Kind {
		return fmt.Errorf("traffic mix kind must be %s", Kind)
	}
	if !namePattern.MatchString(m.Name) {
		return fmt.Errorf("traffic mix name %q must be a lowercase DNS label", m.Name)
	}
	if !namePattern.MatchString(m.Target.Service) {
		return fmt.Errorf("traffic mix %s: target service %q must be a Service name", m.Name, m.Target.Service)
	}
	if m.Target.Port < 1 || m.Target.Port > 65535 {
		return fmt.Errorf("traffic mix %s: target port %d is out of range", m.Name, m.Target.Port)
	}
	if m.Target.Scheme != "http" && m.Target.Scheme != "https" {
		return fmt.Errorf("traffic mix %s: target scheme must be http or https", m.Name)
	}
	if m.RatePerSecond <= 0 || m.RatePerSecond > MaxRatePerSecond {
		return fmt.Errorf("traffic mix %s: ratePerSecond must be in (0, %g]", m.Name, MaxRatePerSecond)
	}
	if err := validateTimeout(m.Timeout); err != nil {
		return fmt.Errorf("traffic mix %s: %w", m.Name, err)
	}
	if err := validateSLO(m.SLO); err != nil {
		return fmt.Errorf("traffic mix %s: %w", m.Name, err)
	}
	if len(m.Routes) == 0 {
		return fmt.Errorf("traffic mix %s: at least one route is required", m.Name)
	}
	seen := make(map[string]bool, len(m.Routes))
	reads := 0
	for _, route := range m.Routes {
		if err := validateRoute(m, route); err != nil {
			return fmt.Errorf("traffic mix %s: route %q: %w", m.Name, route.ID, err)
		}
		if seen[route.ID] {
			return fmt.Errorf("traffic mix %s: duplicate route id %q", m.Name, route.ID)
		}
		seen[route.ID] = true
		if !route.Mutates {
			reads++
		}
	}
	if reads == 0 {
		return fmt.Errorf("traffic mix %s: at least one read route (mutates: false) is required", m.Name)
	}
	return nil
}

func validateRoute(mix Mix, route Route) error {
	if !routeIDPattern.MatchString(route.ID) {
		return fmt.Errorf("id must be lowercase letters, digits, '.', '_', or '-'")
	}
	if err := validateRequest(route.Request); err != nil {
		return err
	}
	if route.Weight < 1 || route.Weight > 100 {
		return fmt.Errorf("weight must be in [1, 100]")
	}
	if err := validateTimeout(route.Timeout); err != nil {
		return err
	}
	for _, status := range route.Expect.Status {
		if status < 100 || status > 599 {
			return fmt.Errorf("expected status %d is not an HTTP status", status)
		}
	}
	if route.SLO != nil {
		if err := validateSLO(mix.RouteSLO(route)); err != nil {
			return err
		}
	}
	if !route.Mutates {
		if route.DataPolicy != "" || route.SyntheticMarker != "" || route.Cleanup != nil {
			return fmt.Errorf("dataPolicy, syntheticMarker, and cleanup apply only to routes with mutates: true")
		}
		return nil
	}
	if route.DataPolicy != DataPolicyIdempotent && route.DataPolicy != DataPolicySelfCleaning {
		return fmt.Errorf("a mutating route needs dataPolicy %q or %q", DataPolicyIdempotent, DataPolicySelfCleaning)
	}
	if strings.TrimSpace(route.SyntheticMarker) == "" {
		return fmt.Errorf("a mutating route needs a syntheticMarker naming its dedicated synthetic data")
	}
	if !requestMentions(route.Request, route.SyntheticMarker) {
		return fmt.Errorf("syntheticMarker %q must appear in the route's query, body, or headers", route.SyntheticMarker)
	}
	if route.DataPolicy == DataPolicySelfCleaning {
		if route.Cleanup == nil {
			return fmt.Errorf("a self-cleaning route needs a cleanup request")
		}
		if err := validateRequest(*route.Cleanup); err != nil {
			return fmt.Errorf("cleanup: %w", err)
		}
		if !requestMentions(*route.Cleanup, route.SyntheticMarker) {
			return fmt.Errorf("cleanup must target the syntheticMarker %q", route.SyntheticMarker)
		}
	} else if route.Cleanup != nil {
		return fmt.Errorf("cleanup applies only to dataPolicy %q", DataPolicySelfCleaning)
	}
	return nil
}

func validateRequest(request Request) error {
	if !methods[request.Method] {
		return fmt.Errorf("method %q is not one of GET, HEAD, POST, PUT, PATCH, DELETE", request.Method)
	}
	if !strings.HasPrefix(request.Path, "/") || strings.HasPrefix(request.Path, "//") ||
		strings.Contains(request.Path, "://") || strings.ContainsAny(request.Path, "?# \t\r\n") {
		return fmt.Errorf("path %q must be an absolute path on the target, without a host, query, or fragment", request.Path)
	}
	for name := range request.Headers {
		if strings.EqualFold(name, "Host") {
			return fmt.Errorf("headers may not override Host")
		}
	}
	return nil
}

func requestMentions(request Request, marker string) bool {
	if strings.Contains(request.Body, marker) {
		return true
	}
	for _, values := range []map[string]string{request.Query, request.Headers} {
		for key, value := range values {
			if strings.Contains(key, marker) || strings.Contains(value, marker) {
				return true
			}
		}
	}
	return false
}

func validateTimeout(timeout Duration) error {
	if timeout.Duration() <= 0 || timeout.Duration() > MaxTimeout {
		return fmt.Errorf("timeout must be in (0, %s]", MaxTimeout)
	}
	return nil
}

func validateSLO(slo SLO) error {
	switch {
	case slo.Window < 1 || slo.Window > 100:
		return fmt.Errorf("slo window must be in [1, 100] samples")
	case slo.MinSamples < 1 || slo.MinSamples > slo.Window:
		return fmt.Errorf("slo minSamples must be in [1, window]")
	case slo.MaxAge.Duration() <= 0 || slo.MaxAge.Duration() > 10*time.Minute:
		return fmt.Errorf("slo maxAge must be in (0, 10m]")
	case slo.MaxErrorRate <= 0 || slo.MaxErrorRate > 1:
		return fmt.Errorf("slo maxErrorRate must be in (0, 1]")
	case slo.MaxTimeoutRate <= 0 || slo.MaxTimeoutRate > 1:
		return fmt.Errorf("slo maxTimeoutRate must be in (0, 1]")
	case slo.LatencyPercentile < 1 || slo.LatencyPercentile > 100:
		return fmt.Errorf("slo latencyPercentile must be in [1, 100]")
	case slo.MaxLatency.Duration() <= 0:
		return fmt.Errorf("slo maxLatency must be positive")
	}
	return nil
}
