package controller

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

func TestIncidentRequestGoldenFixtureRoundTrip(t *testing.T) {
	var request IncidentRequest
	payload := roundTripGoldenFixture(t, "incident_request.json", &request)

	if request.SchemaVersion != ProtocolSchemaVersion {
		t.Fatalf("unexpected schema version %q", request.SchemaVersion)
	}
	if got := request.Findings[0].ParameterBindings["missing_config_map"].Name; got != "geo-config" {
		t.Fatalf("unexpected missing ConfigMap binding %q", got)
	}
	if got := payload["cancellation_token"]; got != "cancel-inc-20260709-0001" {
		t.Fatalf("unexpected cancellation token %#v", got)
	}
}

func TestIncidentResultGoldenFixtureRoundTrip(t *testing.T) {
	var result IncidentResult
	roundTripGoldenFixture(t, "incident_result.json", &result)

	if result.Status != IncidentCompleted {
		t.Fatalf("unexpected incident status %q", result.Status)
	}
	if len(result.VerificationEvidence) != 1 || !result.VerificationEvidence[0].Passed {
		t.Fatalf("unexpected verification evidence %#v", result.VerificationEvidence)
	}
}

func TestSurfacedPlaybookOmitsAbsentBindingsForPythonProtocolDefault(t *testing.T) {
	payload, err := json.Marshal(SurfacedPlaybook{Path: ".sdo/playbooks/health-objective/README.md"})
	if err != nil {
		t.Fatalf("marshal playbook: %v", err)
	}
	if string(payload) != `{"path":".sdo/playbooks/health-objective/README.md"}` {
		t.Fatalf("nil bindings must be omitted, got %s", payload)
	}
}

func roundTripGoldenFixture(t *testing.T, name string, target any) map[string]any {
	t.Helper()
	path := filepath.Join("..", "..", "tests", "fixtures", "sdo", "protocol", name)
	content, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read golden fixture: %v", err)
	}
	var original map[string]any
	if err := json.Unmarshal(content, &original); err != nil {
		t.Fatalf("decode original fixture: %v", err)
	}
	if err := json.Unmarshal(content, target); err != nil {
		t.Fatalf("decode typed fixture: %v", err)
	}
	encoded, err := json.Marshal(target)
	if err != nil {
		t.Fatalf("encode typed fixture: %v", err)
	}
	var roundTripped map[string]any
	if err := json.Unmarshal(encoded, &roundTripped); err != nil {
		t.Fatalf("decode round-tripped fixture: %v", err)
	}
	if !reflect.DeepEqual(roundTripped, original) {
		t.Fatalf("protocol round trip changed payload:\noriginal: %#v\nround trip: %#v", original, roundTripped)
	}
	return roundTripped
}
