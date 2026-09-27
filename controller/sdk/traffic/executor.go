package traffic

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"sort"
	"strings"
	"time"
)

const (
	// SyntheticHeader marks every synthetic request so application logs and
	// the benchmark's own workload analysis can tell it apart from users.
	SyntheticHeader = "X-SDO-Synthetic"
	// MaxErrorExcerpt bounds the response body or transport error kept per sample.
	MaxErrorExcerpt = 240
	maxBodyRead     = 64 * 1024
)

// Outcome classifies one probe.
type Outcome string

const (
	OutcomeOK      Outcome = "ok"
	OutcomeError   Outcome = "error"
	OutcomeTimeout Outcome = "timeout"
)

// Sample is the observed result of one synthetic request.
type Sample struct {
	At      time.Time     `json:"at"`
	Latency time.Duration `json:"latency"`
	// Status is the HTTP status, or 0 when no response arrived.
	Status  int     `json:"status,omitempty"`
	Outcome Outcome `json:"outcome"`
	// Error explains a failed sample: the transport error, or a bounded
	// excerpt of the unexpected response body.
	Error string `json:"error,omitempty"`
}

// Doer sends HTTP requests; *http.Client satisfies it.
type Doer interface {
	Do(*http.Request) (*http.Response, error)
}

// BuildRequest renders request against baseURL (scheme://host:port) with a
// canonical, sorted query string.
func BuildRequest(ctx context.Context, baseURL string, request Request) (*http.Request, error) {
	base, err := url.Parse(baseURL)
	if err != nil {
		return nil, fmt.Errorf("parse base URL: %w", err)
	}
	target := *base
	target.Path = request.Path
	query := url.Values{}
	for key, value := range request.Query {
		query.Set(key, value)
	}
	target.RawQuery = query.Encode()
	var body io.Reader
	if request.Body != "" {
		body = strings.NewReader(request.Body)
	}
	built, err := http.NewRequestWithContext(ctx, request.Method, target.String(), body)
	if err != nil {
		return nil, err
	}
	names := make([]string, 0, len(request.Headers))
	for name := range request.Headers {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		built.Header.Set(name, request.Headers[name])
	}
	built.Header.Set(SyntheticHeader, "1")
	return built, nil
}

// Execute sends one route's request and classifies the response against the
// route's expectation. It never returns an error: every failure is a sample.
func Execute(ctx context.Context, client Doer, baseURL string, route Route, timeout time.Duration) Sample {
	started := time.Now()
	sample := Sample{At: started.UTC()}
	requestCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	request, err := BuildRequest(requestCtx, baseURL, route.Request)
	if err != nil {
		sample.Outcome, sample.Error = OutcomeError, bounded(err.Error())
		sample.Latency = positiveSince(started)
		return sample
	}
	response, err := client.Do(request)
	if err != nil {
		sample.Latency = positiveSince(started)
		if isTimeout(requestCtx, err) {
			sample.Outcome, sample.Error = OutcomeTimeout, bounded(err.Error())
			return sample
		}
		sample.Outcome, sample.Error = OutcomeError, bounded(err.Error())
		return sample
	}
	defer response.Body.Close()
	payload, readErr := io.ReadAll(io.LimitReader(response.Body, maxBodyRead))
	sample.Latency = positiveSince(started)
	sample.Status = response.StatusCode
	if readErr != nil {
		if isTimeout(requestCtx, readErr) {
			sample.Outcome, sample.Error = OutcomeTimeout, bounded(readErr.Error())
			return sample
		}
		sample.Outcome, sample.Error = OutcomeError, bounded(readErr.Error())
		return sample
	}
	sample.Outcome, sample.Error = Classify(route.Expect, response.StatusCode, string(payload))
	return sample
}

// Classify judges a received response against an expectation.
func Classify(expect Expect, status int, body string) (Outcome, string) {
	if !statusExpected(expect, status) {
		excerpt := strings.TrimSpace(body)
		if excerpt == "" {
			excerpt = http.StatusText(status)
		}
		return OutcomeError, bounded(excerpt)
	}
	if expect.BodyContains != "" && !strings.Contains(body, expect.BodyContains) {
		return OutcomeError, bounded(fmt.Sprintf("response lacks %q: %s", expect.BodyContains, strings.TrimSpace(body)))
	}
	return OutcomeOK, ""
}

func statusExpected(expect Expect, status int) bool {
	if len(expect.Status) == 0 {
		return status >= 200 && status < 300
	}
	for _, candidate := range expect.Status {
		if candidate == status {
			return true
		}
	}
	return false
}

func isTimeout(ctx context.Context, err error) bool {
	if errors.Is(err, context.DeadlineExceeded) || errors.Is(ctx.Err(), context.DeadlineExceeded) {
		return true
	}
	var timeout interface{ Timeout() bool }
	return errors.As(err, &timeout) && timeout.Timeout()
}

func positiveSince(started time.Time) time.Duration {
	elapsed := time.Since(started)
	if elapsed <= 0 {
		return time.Nanosecond
	}
	return elapsed
}

func bounded(text string) string {
	text = strings.Join(strings.Fields(text), " ")
	if len(text) <= MaxErrorExcerpt {
		return text
	}
	return text[:MaxErrorExcerpt] + "…"
}
