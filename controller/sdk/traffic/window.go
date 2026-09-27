package traffic

import (
	"fmt"
	"math"
	"sort"
	"strconv"
	"time"

	"sdo.dev/controller/sdk"
)

// Watch is the watch kind the controller runtime emits when synthetic probe
// results may change a route's verdict. Traffic detectors declare it so they
// are evaluated as soon as probes fail or recover, not on their interval.
var Watch = sdk.WatchKind{APIVersion: APIVersion, Kind: "SyntheticTraffic"}

// RouteObservations are one route's recent samples, oldest first.
type RouteObservations struct {
	RouteID string   `json:"route_id"`
	Samples []Sample `json:"samples"`
	// Qualified reports whether the route has succeeded at least once since
	// observation began. A route that never succeeded is not a health signal:
	// the mix may name a path this deployment does not serve.
	Qualified bool `json:"qualified"`
}

// Window is what the runtime has observed for one mix at ObservedAt.
type Window struct {
	Mix        Mix                 `json:"mix"`
	ObservedAt time.Time           `json:"observed_at"`
	Routes     []RouteObservations `json:"routes"`
	// LoadError is set when the runtime could not load the mix.
	LoadError string `json:"load_error,omitempty"`
}

// Source is implemented by detection contexts that carry synthetic-traffic
// observations. The controller runtime's snapshots implement it.
type Source interface {
	TrafficWindow(mix string) (Window, bool)
}

// WindowFrom returns the observations of mix, when the context carries them.
func WindowFrom(ctx sdk.DetectionContext, mix string) (Window, bool) {
	source, ok := ctx.(Source)
	if !ok {
		return Window{}, false
	}
	return source.TrafficWindow(mix)
}

// Verdict is one route's SLO judgement over its recent samples.
type Verdict struct {
	RouteID     string
	Evaluated   bool
	Healthy     bool
	Samples     int
	Errors      int
	Timeouts    int
	ErrorRate   float64
	TimeoutRate float64
	Latency     time.Duration
	SLO         SLO
	Violations  []string
	// StatusCounts counts samples by HTTP status, "transport" for failures
	// with no response, and "timeout".
	StatusCounts   map[string]int
	RecentFailures []Sample
	Span           time.Duration
}

// Evaluate judges samples (oldest first) of one route at now. Samples older
// than the SLO's maxAge are ignored, only the latest window samples count, and
// fewer than minSamples leaves the route unevaluated, which is healthy.
func Evaluate(routeID string, slo SLO, samples []Sample, now time.Time) Verdict {
	verdict := Verdict{RouteID: routeID, Healthy: true, SLO: slo, StatusCounts: map[string]int{}}
	recent := make([]Sample, 0, len(samples))
	for _, sample := range samples {
		if now.Sub(sample.At) <= slo.MaxAge.Duration() {
			recent = append(recent, sample)
		}
	}
	if len(recent) > slo.Window {
		recent = recent[len(recent)-slo.Window:]
	}
	verdict.Samples = len(recent)
	if len(recent) < slo.MinSamples {
		return verdict
	}
	verdict.Evaluated = true
	verdict.Span = recent[len(recent)-1].At.Sub(recent[0].At)
	latencies := make([]time.Duration, 0, len(recent))
	for _, sample := range recent {
		switch sample.Outcome {
		case OutcomeTimeout:
			verdict.Timeouts++
			verdict.StatusCounts["timeout"]++
		case OutcomeError:
			verdict.Errors++
			latencies = append(latencies, sample.Latency)
		default:
			latencies = append(latencies, sample.Latency)
		}
		if sample.Outcome != OutcomeTimeout {
			key := "transport"
			if sample.Status > 0 {
				key = strconv.Itoa(sample.Status)
			}
			verdict.StatusCounts[key]++
		}
		if sample.Outcome != OutcomeOK {
			verdict.RecentFailures = append(verdict.RecentFailures, sample)
		}
	}
	if len(verdict.RecentFailures) > 3 {
		verdict.RecentFailures = verdict.RecentFailures[len(verdict.RecentFailures)-3:]
	}
	total := float64(len(recent))
	verdict.ErrorRate = float64(verdict.Errors+verdict.Timeouts) / total
	verdict.TimeoutRate = float64(verdict.Timeouts) / total
	verdict.Latency = percentile(latencies, slo.LatencyPercentile)
	if verdict.ErrorRate >= slo.MaxErrorRate {
		verdict.Violations = append(verdict.Violations, fmt.Sprintf(
			"error rate %d/%d ≥ %s", verdict.Errors+verdict.Timeouts, len(recent), percent(slo.MaxErrorRate)))
	}
	if verdict.TimeoutRate >= slo.MaxTimeoutRate {
		verdict.Violations = append(verdict.Violations, fmt.Sprintf(
			"timeout rate %d/%d ≥ %s", verdict.Timeouts, len(recent), percent(slo.MaxTimeoutRate)))
	}
	if len(latencies) > 0 && verdict.Latency > slo.MaxLatency.Duration() {
		verdict.Violations = append(verdict.Violations, fmt.Sprintf(
			"p%d latency %s > %s", slo.LatencyPercentile, verdict.Latency.Round(time.Millisecond), slo.MaxLatency.Duration()))
	}
	verdict.Healthy = len(verdict.Violations) == 0
	return verdict
}

// percentile uses the nearest-rank method.
func percentile(values []time.Duration, rank int) time.Duration {
	if len(values) == 0 {
		return 0
	}
	sorted := append([]time.Duration(nil), values...)
	sort.Slice(sorted, func(left int, right int) bool { return sorted[left] < sorted[right] })
	index := int(math.Ceil(float64(rank)/100*float64(len(sorted)))) - 1
	if index < 0 {
		index = 0
	}
	return sorted[index]
}

func percent(value float64) string {
	return strconv.FormatFloat(value*100, 'f', -1, 64) + "%"
}
