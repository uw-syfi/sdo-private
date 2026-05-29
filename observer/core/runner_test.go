package core

import (
	"bytes"
	"context"
	"testing"

	"sds.dev/observer/sdk"
	"sds.dev/observer/sdk/sdktest"
)

type testDetector struct {
	id string
}

func (d testDetector) Spec() sdk.DetectorSpec {
	return sdk.DetectorSpec{ID: d.id}
}

func (d testDetector) Detect(context.Context, sdk.DetectionContext) ([]sdk.Finding, error) {
	return []sdk.Finding{
		{
			RuleID:   d.id,
			Status:   sdk.FindingActive,
			Severity: sdk.SeverityWarning,
			Summary:  "test finding",
			Evidence: "test evidence",
			PrimaryResource: sdk.ObjectRef{
				Kind: "Service",
				Name: "api",
			},
		},
	}, nil
}

func TestValidateDetectorsRejectsDuplicateIDs(t *testing.T) {
	err := ValidateDetectors([]sdk.Detector{
		testDetector{id: "same"},
		testDetector{id: "same"},
	})
	if err == nil {
		t.Fatal("expected duplicate detector error")
	}
}

func TestRunnerEmitsFindingsAsJSON(t *testing.T) {
	var output bytes.Buffer
	runner := Runner{
		Detectors: []sdk.Detector{testDetector{id: "missing-endpoints"}},
		Provider:  StaticSnapshotProvider{SnapshotValue: sdktest.Snapshot{NamespaceName: "demo"}},
		Sink:      JSONSink{Writer: &output},
	}

	if err := runner.RunOnce(context.Background()); err != nil {
		t.Fatalf("run once: %v", err)
	}
	if !bytes.Contains(output.Bytes(), []byte(`"rule_id":"missing-endpoints"`)) {
		t.Fatalf("expected JSON finding, got %s", output.String())
	}
}

func TestRunWithOptionsCanValidateDetectorsOnly(t *testing.T) {
	var stdout bytes.Buffer
	err := RunWithOptions(context.Background(), []sdk.Detector{testDetector{id: "ok"}}, RuntimeOptions{
		Args:   []string{"--validate-detectors"},
		Stdout: &stdout,
	})
	if err != nil {
		t.Fatalf("validate detectors: %v", err)
	}
	if got := stdout.String(); got != "validated 1 detector(s)\n" {
		t.Fatalf("unexpected output %q", got)
	}
}
