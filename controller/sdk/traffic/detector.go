package traffic

import (
	"context"
	"fmt"
	"sort"
	"strings"
	"time"

	"sdo.dev/controller/sdk"
)

// Consumer is implemented by detectors that evaluate synthetic traffic. The
// controller runtime probes exactly the mixes its detectors consume.
type Consumer interface {
	TrafficMixes() []string
}

// RuleIDPrefix prefixes the rule ID of each route finding ("route-slo.<id>").
const RuleIDPrefix = "route-slo."

type sloDetector struct {
	spec sdk.DetectorSpec
	mix  string
}

// NewDetector returns a health detector that reports every qualified route of
// mix that violates its SLO. It decides purely from the runtime's observed
// samples; with no observations it reports nothing.
func NewDetector(spec sdk.DetectorSpec, mix string) sdk.Detector {
	return sloDetector{spec: spec, mix: mix}
}

func (d sloDetector) Spec() sdk.DetectorSpec { return d.spec }

func (d sloDetector) TrafficMixes() []string { return []string{d.mix} }

func (d sloDetector) Detect(_ context.Context, ctx sdk.DetectionContext) ([]sdk.Finding, error) {
	window, ok := WindowFrom(ctx, d.mix)
	if !ok {
		return nil, nil
	}
	if window.LoadError != "" {
		return nil, fmt.Errorf("traffic mix %s: %s", d.mix, window.LoadError)
	}
	target := sdk.ObjectRef{APIVersion: "v1", Kind: "Service", Namespace: ctx.Namespace(), Name: window.Mix.Target.Service}
	findings := make([]sdk.Finding, 0)
	for _, observed := range window.Routes {
		route, known := window.Mix.Route(observed.RouteID)
		if !known || !observed.Qualified {
			continue
		}
		verdict := Evaluate(route.ID, window.Mix.RouteSLO(route), observed.Samples, window.ObservedAt)
		if verdict.Healthy {
			continue
		}
		findings = append(findings, routeFinding(d.spec, window.Mix, route, verdict, target))
	}
	sort.Slice(findings, func(left int, right int) bool { return findings[left].RuleID < findings[right].RuleID })
	return findings, nil
}

func routeFinding(spec sdk.DetectorSpec, mix Mix, route Route, verdict Verdict, target sdk.ObjectRef) sdk.Finding {
	request := route.Method + " " + route.Path
	statusKeys := make([]string, 0, len(verdict.StatusCounts))
	for key := range verdict.StatusCounts {
		statusKeys = append(statusKeys, key)
	}
	sort.Strings(statusKeys)
	statusParts := make([]string, 0, len(statusKeys))
	for _, key := range statusKeys {
		label := key
		if key != "transport" && key != "timeout" {
			label = "HTTP " + key
		}
		statusParts = append(statusParts, fmt.Sprintf("%s ×%d", label, verdict.StatusCounts[key]))
	}
	failures := make([]string, 0, len(verdict.RecentFailures))
	for _, sample := range verdict.RecentFailures {
		label := string(sample.Outcome)
		if sample.Status > 0 {
			label = fmt.Sprintf("HTTP %d", sample.Status)
		}
		failures = append(failures, fmt.Sprintf("[%s] %s: %s", sample.At.UTC().Format(time.RFC3339), label, sample.Error))
	}
	evidence := fmt.Sprintf(
		"synthetic %s to Service %s port %d, last %d samples over %s: %s; p%d latency %s. Recent failures: %s",
		request, mix.Target.Service, mix.Target.Port, verdict.Samples, verdict.Span.Round(time.Millisecond),
		strings.Join(statusParts, ", "), verdict.SLO.LatencyPercentile, verdict.Latency.Round(time.Millisecond),
		strings.Join(failures, "; "),
	)
	playbooks := append([]string(nil), spec.Playbooks...)
	return sdk.Finding{
		DetectorID: spec.ID,
		RuleID:     RuleIDPrefix + route.ID,
		Status:     sdk.FindingActive,
		Severity:   sdk.SeverityCritical,
		Summary: fmt.Sprintf("synthetic route %s (%s) violates its SLO: %s",
			route.ID, request, strings.Join(verdict.Violations, "; ")),
		Evidence:        evidence,
		PrimaryResource: target,
		Playbooks:       playbooks,
		Metadata: map[string]any{
			"mix":                mix.Name,
			"route":              route.ID,
			"method":             route.Method,
			"path":               route.Path,
			"mutates":            route.Mutates,
			"samples":            verdict.Samples,
			"errors":             verdict.Errors,
			"timeouts":           verdict.Timeouts,
			"error_rate":         verdict.ErrorRate,
			"timeout_rate":       verdict.TimeoutRate,
			"latency_percentile": verdict.SLO.LatencyPercentile,
			"latency_ms":         verdict.Latency.Milliseconds(),
			"status_counts":      verdict.StatusCounts,
			"violations":         verdict.Violations,
		},
	}
}
