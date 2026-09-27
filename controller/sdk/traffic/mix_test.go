package traffic_test

import (
	"strings"
	"testing"
	"time"

	"sdo.dev/controller/sdk/traffic"
)

const minimalMix = `{
  "apiVersion": "sdo.dev/v1alpha1",
  "kind": "TrafficMix",
  "name": "frontend",
  "target": {"service": "frontend", "port": 5000},
  "routes": [
    {"id": "search", "method": "GET", "path": "/hotels", "query": {"lat": "37.7"}}
  ]
}`

func TestParseMixAppliesDocumentedDefaults(t *testing.T) {
	mix, err := traffic.ParseMixJSON([]byte(minimalMix))
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if mix.Target.Scheme != "http" || mix.RatePerSecond != traffic.DefaultRatePerSecond ||
		mix.Timeout.Duration() != traffic.DefaultTimeout {
		t.Fatalf("mix defaults not applied: %+v", mix)
	}
	route := mix.Routes[0]
	if route.Weight != 1 || route.Timeout.Duration() != traffic.DefaultTimeout {
		t.Fatalf("route defaults not applied: %+v", route)
	}
	slo := mix.RouteSLO(route)
	if slo.Window != traffic.DefaultWindow || slo.MinSamples != traffic.DefaultMinSamples ||
		slo.MaxErrorRate != traffic.DefaultMaxErrorRate || slo.MaxTimeoutRate != traffic.DefaultMaxTimeoutRate ||
		slo.LatencyPercentile != traffic.DefaultLatencyPercentile || slo.MaxLatency.Duration() != traffic.DefaultMaxLatency ||
		slo.MaxAge.Duration() != traffic.DefaultMaxAge {
		t.Fatalf("SLO defaults not applied: %+v", slo)
	}
}

func TestRouteSLOOverridesOnlyTheFieldsItSets(t *testing.T) {
	mix, err := traffic.ParseMixJSON([]byte(`{
	  "apiVersion": "sdo.dev/v1alpha1", "kind": "TrafficMix", "name": "web",
	  "target": {"service": "web", "port": 80},
	  "slo": {"window": 8, "maxErrorRate": 0.25},
	  "routes": [
	    {"id": "home", "method": "GET", "path": "/"},
	    {"id": "slow", "method": "GET", "path": "/report", "slo": {"maxLatency": "5s"}}
	  ]
	}`))
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	home := mix.RouteSLO(mix.Routes[0])
	slow := mix.RouteSLO(mix.Routes[1])
	if home.Window != 8 || home.MaxErrorRate != 0.25 || home.MaxLatency.Duration() != traffic.DefaultMaxLatency {
		t.Fatalf("mix-level SLO not applied: %+v", home)
	}
	if slow.Window != 8 || slow.MaxErrorRate != 0.25 || slow.MaxLatency.Duration() != 5*time.Second {
		t.Fatalf("route override not merged over mix SLO: %+v", slow)
	}
}

func TestParseMixRejectsUnsafeOrAmbiguousMixes(t *testing.T) {
	cases := map[string]struct {
		mix  string
		want string
	}{
		"unknown field": {
			mix:  strings.Replace(minimalMix, `"name": "frontend",`, `"name": "frontend", "extra": 1,`, 1),
			want: "unknown field",
		},
		"wrong kind": {
			mix:  strings.Replace(minimalMix, `"TrafficMix"`, `"Other"`, 1),
			want: "kind",
		},
		"invalid name": {
			mix:  strings.Replace(minimalMix, `"name": "frontend"`, `"name": "Front End"`, 1),
			want: "name",
		},
		"absolute URL path": {
			mix:  strings.Replace(minimalMix, `"/hotels"`, `"http://evil.example/hotels"`, 1),
			want: "path",
		},
		"missing target port": {
			mix:  strings.Replace(minimalMix, `"port": 5000`, `"port": 0`, 1),
			want: "port",
		},
		"rate above cap": {
			mix:  strings.Replace(minimalMix, `"routes"`, `"ratePerSecond": 500, "routes"`, 1),
			want: "ratePerSecond",
		},
		"error rate above one": {
			mix:  strings.Replace(minimalMix, `"routes"`, `"slo": {"maxErrorRate": 1.5}, "routes"`, 1),
			want: "maxErrorRate",
		},
		"write route without data policy": {
			mix: strings.Replace(minimalMix, `"query": {"lat": "37.7"}}`,
				`"query": {"lat": "37.7"}}, {"id": "book", "method": "POST", "path": "/book", "mutates": true, "syntheticMarker": "sdo-synthetic", "body": "sdo-synthetic"}`, 1),
			want: "dataPolicy",
		},
		"write route without synthetic marker": {
			mix: strings.Replace(minimalMix, `"query": {"lat": "37.7"}}`,
				`"query": {"lat": "37.7"}}, {"id": "book", "method": "POST", "path": "/book", "mutates": true, "dataPolicy": "idempotent"}`, 1),
			want: "syntheticMarker",
		},
		"synthetic marker absent from the request": {
			mix: strings.Replace(minimalMix, `"query": {"lat": "37.7"}}`,
				`"query": {"lat": "37.7"}}, {"id": "book", "method": "POST", "path": "/book", "mutates": true, "dataPolicy": "idempotent", "syntheticMarker": "sdo-synthetic"}`, 1),
			want: "syntheticMarker",
		},
		"self-cleaning write without cleanup": {
			mix: strings.Replace(minimalMix, `"query": {"lat": "37.7"}}`,
				`"query": {"lat": "37.7"}}, {"id": "book", "method": "POST", "path": "/book", "mutates": true, "dataPolicy": "self-cleaning", "syntheticMarker": "sdo-synthetic", "body": "sdo-synthetic"}`, 1),
			want: "cleanup",
		},
		"no read route": {
			mix: strings.Replace(minimalMix, `{"id": "search", "method": "GET", "path": "/hotels", "query": {"lat": "37.7"}}`,
				`{"id": "book", "method": "POST", "path": "/book", "mutates": true, "dataPolicy": "idempotent", "syntheticMarker": "sdo-synthetic", "body": "sdo-synthetic"}`, 1),
			want: "read route",
		},
		"duplicate route id": {
			mix: strings.Replace(minimalMix, `"query": {"lat": "37.7"}}`,
				`"query": {"lat": "37.7"}}, {"id": "search", "method": "GET", "path": "/other"}`, 1),
			want: "duplicate",
		},
	}
	for name, test := range cases {
		t.Run(name, func(t *testing.T) {
			_, err := traffic.ParseMixJSON([]byte(test.mix))
			if err == nil || !strings.Contains(err.Error(), test.want) {
				t.Fatalf("expected error containing %q, got %v", test.want, err)
			}
		})
	}
}

func TestParseMixAcceptsIdempotentAndSelfCleaningWrites(t *testing.T) {
	mix, err := traffic.ParseMixJSON([]byte(strings.Replace(minimalMix, `"query": {"lat": "37.7"}}`,
		`"query": {"lat": "37.7"}},
		 {"id": "reserve", "method": "GET", "path": "/reservation", "mutates": true, "dataPolicy": "idempotent",
		  "syntheticMarker": "sdo-synthetic", "query": {"customerName": "sdo-synthetic", "number": "0"}},
		 {"id": "cart", "method": "POST", "path": "/cart", "mutates": true, "dataPolicy": "self-cleaning",
		  "syntheticMarker": "sdo-synthetic", "body": "{\"user\":\"sdo-synthetic\"}",
		  "cleanup": {"method": "DELETE", "path": "/cart", "query": {"user": "sdo-synthetic"}}}`, 1)))
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if len(mix.Routes) != 3 || !mix.Routes[1].Mutates || mix.Routes[2].Cleanup == nil {
		t.Fatalf("write routes not preserved: %+v", mix.Routes)
	}
}
