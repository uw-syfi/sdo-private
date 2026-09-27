package traffic_test

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"sdo.dev/controller/sdk/traffic"
)

func TestBuildRequestIsDeterministic(t *testing.T) {
	route := traffic.Route{
		ID: "search",
		Request: traffic.Request{
			Method: "GET", Path: "/hotels",
			Query:   map[string]string{"outDate": "2015-04-10", "inDate": "2015-04-09", "lat": "37.7"},
			Headers: map[string]string{"X-Synthetic": "sdo"},
		},
	}
	first, err := traffic.BuildRequest(context.Background(), "http://frontend.hotel.svc:5000", route.Request)
	if err != nil {
		t.Fatalf("build: %v", err)
	}
	second, _ := traffic.BuildRequest(context.Background(), "http://frontend.hotel.svc:5000", route.Request)
	if first.URL.String() != second.URL.String() ||
		first.URL.String() != "http://frontend.hotel.svc:5000/hotels?inDate=2015-04-09&lat=37.7&outDate=2015-04-10" {
		t.Fatalf("request URL must be canonical, got %q and %q", first.URL, second.URL)
	}
	if first.Header.Get("X-Synthetic") != "sdo" || first.Header.Get(traffic.SyntheticHeader) == "" {
		t.Fatalf("request must carry route headers and the synthetic-traffic marker header, got %v", first.Header)
	}
}

func TestExecuteClassifiesResponses(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		switch request.URL.Path {
		case "/ok":
			_, _ = io.WriteString(writer, `{"message":"Login successfully!"}`)
		case "/wrong-body":
			_, _ = io.WriteString(writer, `{"message":"Failed. Please check your username and password."}`)
		case "/error":
			http.Error(writer, "rpc error: code = Unavailable desc = connection error", http.StatusInternalServerError)
		case "/slow":
			time.Sleep(200 * time.Millisecond)
			_, _ = io.WriteString(writer, "late")
		}
	}))
	defer server.Close()
	expect := traffic.Expect{BodyContains: "Login successfully!"}
	cases := map[string]struct {
		path    string
		expect  traffic.Expect
		timeout time.Duration
		outcome traffic.Outcome
		status  int
		error   string
	}{
		"success":            {path: "/ok", expect: expect, timeout: time.Second, outcome: traffic.OutcomeOK, status: 200},
		"body mismatch":      {path: "/wrong-body", expect: expect, timeout: time.Second, outcome: traffic.OutcomeError, status: 200, error: "Failed. Please check"},
		"server error":       {path: "/error", timeout: time.Second, outcome: traffic.OutcomeError, status: 500, error: "rpc error"},
		"deadline exhausted": {path: "/slow", timeout: 20 * time.Millisecond, outcome: traffic.OutcomeTimeout},
	}
	for name, test := range cases {
		t.Run(name, func(t *testing.T) {
			route := traffic.Route{ID: "r", Request: traffic.Request{Method: "GET", Path: test.path}, Expect: test.expect}
			sample := traffic.Execute(context.Background(), server.Client(), server.URL, route, test.timeout)
			if sample.Outcome != test.outcome || sample.Status != test.status {
				t.Fatalf("got %+v", sample)
			}
			if test.error != "" && !strings.Contains(sample.Error, test.error) {
				t.Fatalf("sample error %q lacks %q", sample.Error, test.error)
			}
			if sample.At.IsZero() || sample.Latency <= 0 {
				t.Fatalf("sample must record when and how long, got %+v", sample)
			}
		})
	}
}

func TestExecuteReportsConnectionRefused(t *testing.T) {
	server := httptest.NewServer(http.NotFoundHandler())
	address := server.URL
	server.Close()
	route := traffic.Route{ID: "r", Request: traffic.Request{Method: "GET", Path: "/"}}
	sample := traffic.Execute(context.Background(), http.DefaultClient, address, route, time.Second)
	if sample.Outcome != traffic.OutcomeError || !strings.Contains(sample.Error, "refused") {
		t.Fatalf("a closed port must be an error with the transport cause, got %+v", sample)
	}
}

func TestExecuteBoundsTheRecordedBody(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		http.Error(writer, strings.Repeat("x", 10_000), http.StatusBadGateway)
	}))
	defer server.Close()
	route := traffic.Route{ID: "r", Request: traffic.Request{Method: "GET", Path: "/"}}
	sample := traffic.Execute(context.Background(), server.Client(), server.URL, route, time.Second)
	if len(sample.Error) > traffic.MaxErrorExcerpt+64 {
		t.Fatalf("recorded error body must be bounded, got %d bytes", len(sample.Error))
	}
}
