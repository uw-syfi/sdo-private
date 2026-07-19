package runtime

import (
	"testing"

	"sdo.dev/controller/sdk"
)

func TestFindingStateRequiresTwoFiringsAndTwoClears(t *testing.T) {
	tracker := NewFindingStateTracker(2, 2)
	finding := stateFinding("fault-a")

	if change := tracker.Observe("detector-a", []sdk.Finding{finding}); len(change.Activated) != 0 {
		t.Fatalf("first sample should be transient: %#v", change)
	}
	if change := tracker.Observe("detector-a", []sdk.Finding{finding, finding}); len(change.Activated) != 1 {
		t.Fatalf("second deduplicated sample should activate once: %#v", change)
	}
	if change := tracker.Observe("detector-a", nil); len(change.Cleared) != 0 {
		t.Fatalf("first clear should not resolve: %#v", change)
	}
	if change := tracker.Observe("detector-a", nil); !equalStrings(change.Cleared, []string{FindingStateKey("detector-a", "fault-a")}) {
		t.Fatalf("second clear should resolve: %#v", change)
	}
}

func TestFindingStateSuppressesTransientFiring(t *testing.T) {
	tracker := NewFindingStateTracker(2, 2)
	tracker.Observe("detector-a", []sdk.Finding{stateFinding("transient")})
	tracker.Observe("detector-a", nil)

	if change := tracker.Observe("detector-a", []sdk.Finding{stateFinding("transient")}); len(change.Activated) != 0 {
		t.Fatalf("nonconsecutive firing activated: %#v", change)
	}
}

func TestFindingStateUsesPerDetectorPersistence(t *testing.T) {
	tracker := NewFindingStateTracker(9, 9)
	tracker.SetPolicy("fast", 1, 1)
	tracker.SetPolicy("slow", 3, 2)

	if changes := tracker.Observe("fast", []sdk.Finding{stateFinding("fast-fault")}); len(changes.Activated) != 1 {
		t.Fatalf("fast detector did not use its firing threshold: %#v", changes)
	}
	if changes := tracker.Observe("fast", nil); len(changes.Cleared) != 1 {
		t.Fatalf("fast detector did not use its clear threshold: %#v", changes)
	}
	for sample := 1; sample < 3; sample++ {
		if changes := tracker.Observe("slow", []sdk.Finding{stateFinding("slow-fault")}); len(changes.Activated) != 0 {
			t.Fatalf("slow detector activated on sample %d: %#v", sample, changes)
		}
	}
	if changes := tracker.Observe("slow", []sdk.Finding{stateFinding("slow-fault")}); len(changes.Activated) != 1 {
		t.Fatalf("slow detector did not activate on its third sample: %#v", changes)
	}
}

func stateFinding(fingerprint string) sdk.Finding {
	return sdk.Finding{
		DetectorID: fingerprint,
		RuleID:     fingerprint, Status: sdk.FindingActive, Severity: sdk.SeverityWarning,
		Summary: "fault", Evidence: "evidence",
		PrimaryResource: sdk.ObjectRef{Kind: "Deployment", Name: fingerprint},
		Fingerprint:     fingerprint,
	}
}
