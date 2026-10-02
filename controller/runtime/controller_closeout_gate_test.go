package runtime

import (
	"context"
	"strings"
	"testing"
	"time"

	"sdo.dev/controller/sdk"
	"sdo.dev/controller/sdk/sdktest"
)

// scriptedDispatcher answers each request with whatever the test scripts for
// that attempt, so a test controls which objects a responder repaired.
type scriptedDispatcher struct {
	requests chan IncidentRequest
	answer   func(attempt int, request IncidentRequest) IncidentResult
	attempts int
}

func (d *scriptedDispatcher) Dispatch(_ context.Context, request IncidentRequest) (IncidentResult, error) {
	d.attempts++
	d.requests <- request
	return d.answer(d.attempts, request), nil
}

func repairedResult(request IncidentRequest, resources ...sdk.ObjectRef) IncidentResult {
	result := completedResult(request.IncidentID)
	result.RepairActions = []RepairActionReceipt{{
		ActionID: "a1", Kind: "kubectl", Target: "repair", Resources: resources, Summary: "repair", Success: true,
	}}
	return result
}

var isolationPolicy = StateChange{Kind: "NetworkPolicy", Name: "deny-all-recommendation", Change: StateChangeAdded}

func newCloseoutController(
	t *testing.T, gate bool, maxFollowUps int, changes []StateChange,
	answer func(attempt int, request IncidentRequest) IncidentResult,
) (*Controller, *scriptedDispatcher) {
	t.Helper()
	health := followUpHealthDetector("healtha", true, true, false)
	dispatcher := &scriptedDispatcher{requests: make(chan IncidentRequest, 8), answer: answer}
	config := testControllerConfig()
	config.VerificationTimeout = 30 * time.Second
	config.MaxFollowUps = maxFollowUps
	config.FollowUpCooldown = time.Second
	config.CloseoutStateGate = gate
	controller, err := NewController(
		config, []sdk.Detector{health}, staticProvider{snapshot: sdktest.Snapshot{}}, dispatcher, time.Unix(0, 0),
	)
	if err != nil {
		t.Fatalf("new controller: %v", err)
	}
	controller.Baseline = &recordingBaseline{changes: &StateChanges{
		BaselineAt: time.Unix(0, 0).UTC(), ObservedAt: time.Unix(1, 0).UTC(), Changes: changes,
	}}
	return controller, dispatcher
}

// runToVerification dispatches the incident and steps until health has cleared
// after the responder, returning at the step where verification first passes.
func runToVerification(t *testing.T, controller *Controller, dispatcher *scriptedDispatcher, from int64) IncidentRequest {
	t.Helper()
	if from == 0 {
		stepAt(t, controller, 0)
		stepAt(t, controller, 1)
	}
	executePendingEffect(t, controller)
	request := awaitRequest(t, dispatcher.requests)
	awaitDispatchCompletionQueued(t, controller)
	return request
}

func TestCloseoutGateSendsBackAnUnrepairedStateChange(t *testing.T) {
	controller, dispatcher := newCloseoutController(t, true, 2, []StateChange{isolationPolicy},
		func(_ int, request IncidentRequest) IncidentResult {
			return repairedResult(request, sdk.ObjectRef{Kind: "Deployment", Name: "geo"})
		})
	first := runToVerification(t, controller, dispatcher, 0)
	stepAt(t, controller, 2)
	stepAt(t, controller, 3)
	stepAt(t, controller, 4)
	if _, closed := controller.PendingIncidentClosure(); closed {
		t.Fatal("an unrepaired added object closed the incident")
	}
	effect, ok := controller.PendingDispatchEffect()
	if !ok {
		t.Fatal("the gate did not send the incident back to a responder")
	}
	request := effect.Request
	if request.FollowUp == nil || request.FollowUp.ParentIncidentID != first.IncidentID {
		t.Fatalf("the gate must reuse the bounded follow-up chain: %#v", request.FollowUp)
	}
	if len(request.Findings) != 1 || request.Findings[0].PrimaryResource.Kind != "NetworkPolicy" ||
		request.Findings[0].PrimaryResource.Name != "deny-all-recommendation" {
		t.Fatalf("the follow-up must name the unrepaired object: %#v", request.Findings)
	}
	if request.Findings[0].DetectorID != CloseoutGateDetectorID {
		t.Fatalf("the finding must come from the controller's own gate, got %q", request.Findings[0].DetectorID)
	}
	if !strings.Contains(request.FollowUp.PriorSummary, "NetworkPolicy/deny-all-recommendation") {
		t.Fatalf("the responder must be told why it is back: %q", request.FollowUp.PriorSummary)
	}
}

func TestCloseoutGateClosesWhenARepairTouchedTheObject(t *testing.T) {
	controller, dispatcher := newCloseoutController(t, true, 2, []StateChange{isolationPolicy},
		func(_ int, request IncidentRequest) IncidentResult {
			// Kind matching is case-insensitive: receipts come from kubectl text.
			return repairedResult(request, sdk.ObjectRef{Kind: "networkpolicy", Name: "deny-all-recommendation"})
		})
	runToVerification(t, controller, dispatcher, 0)
	for second := int64(2); second <= 4; second++ {
		stepAt(t, controller, second)
	}
	closure, closed := controller.PendingIncidentClosure()
	if !closed {
		t.Fatal("a repaired object must not hold closure")
	}
	if closure.CloseoutGate != nil {
		t.Fatalf("nothing was left to gate: %#v", closure.CloseoutGate)
	}
}

func TestCloseoutGateIgnoresARepairThatFailed(t *testing.T) {
	controller, dispatcher := newCloseoutController(t, true, 2, []StateChange{isolationPolicy},
		func(_ int, request IncidentRequest) IncidentResult {
			result := repairedResult(request, sdk.ObjectRef{Kind: "NetworkPolicy", Name: "deny-all-recommendation"})
			result.RepairActions[0].Success = false
			return result
		})
	runToVerification(t, controller, dispatcher, 0)
	for second := int64(2); second <= 4; second++ {
		stepAt(t, controller, second)
	}
	if _, closed := controller.PendingIncidentClosure(); closed {
		t.Fatal("a failed repair attempt must not exempt the object")
	}
}

func TestCloseoutGateAcceptsARecordedReasonAndMarksTheClosure(t *testing.T) {
	controller, dispatcher := newCloseoutController(t, true, 2, []StateChange{isolationPolicy},
		func(_ int, request IncidentRequest) IncidentResult {
			result := repairedResult(request, sdk.ObjectRef{Kind: "Deployment", Name: "geo"})
			result.AcknowledgedStateChanges = []StateChangeAcknowledgement{{
				Kind: "NetworkPolicy", Name: "deny-all-recommendation", Reason: "pre-existing isolation for an unrelated tier",
			}}
			return result
		})
	runToVerification(t, controller, dispatcher, 0)
	for second := int64(2); second <= 4; second++ {
		stepAt(t, controller, second)
	}
	closure, closed := controller.PendingIncidentClosure()
	if !closed {
		t.Fatal("an acknowledged object must not hold closure")
	}
	gate := closure.CloseoutGate
	if gate == nil || gate.Outcome != CloseoutAcknowledged || len(gate.Objects) != 1 ||
		gate.Objects[0].Kind != "NetworkPolicy" || gate.Objects[0].Reason == "" {
		t.Fatalf("the closure must record the justification: %#v", gate)
	}
}

func TestCloseoutGateIsBoundedAndMarksExhaustion(t *testing.T) {
	controller, dispatcher := newCloseoutController(t, true, 1, []StateChange{isolationPolicy},
		func(_ int, request IncidentRequest) IncidentResult {
			return repairedResult(request, sdk.ObjectRef{Kind: "Deployment", Name: "geo"})
		})
	runToVerification(t, controller, dispatcher, 0)
	for second := int64(2); second <= 4; second++ {
		stepAt(t, controller, second)
	}
	runToVerification(t, controller, dispatcher, 2)
	for second := int64(5); second <= 9; second++ {
		stepAt(t, controller, second)
	}
	closure, closed := controller.PendingIncidentClosure()
	if !closed {
		t.Fatal("with the follow-up budget spent the gate must let the incident close")
	}
	if dispatcher.attempts != 2 {
		t.Fatalf("the gate sent the incident back %d times, want exactly one follow-up", dispatcher.attempts-1)
	}
	gate := closure.CloseoutGate
	if gate == nil || gate.Outcome != CloseoutExhausted || len(gate.Objects) != 1 ||
		gate.Objects[0].Name != "deny-all-recommendation" {
		t.Fatalf("the closure must mark the unrepaired object: %#v", gate)
	}
	if closure.Request.FollowUp == nil || closure.Request.FollowUp.Attempt != 1 {
		t.Fatalf("closure should come from the follow-up: %#v", closure.Request.FollowUp)
	}
}

func TestCloseoutGateCreditsRepairsAcrossTheFollowUpChain(t *testing.T) {
	second := StateChange{Kind: "ConfigMap", Name: "mongo-rate-script", Change: StateChangeModified}
	controller, dispatcher := newCloseoutController(t, true, 2, []StateChange{isolationPolicy, second},
		func(attempt int, request IncidentRequest) IncidentResult {
			if attempt == 1 {
				return repairedResult(request, sdk.ObjectRef{Kind: "ConfigMap", Name: "mongo-rate-script"})
			}
			return repairedResult(request, sdk.ObjectRef{Kind: "NetworkPolicy", Name: "deny-all-recommendation"})
		})
	runToVerification(t, controller, dispatcher, 0)
	for step := int64(2); step <= 4; step++ {
		stepAt(t, controller, step)
	}
	effect, ok := controller.PendingDispatchEffect()
	if !ok {
		t.Fatal("the unrepaired object must be sent back")
	}
	if len(effect.Request.Findings) != 1 || effect.Request.Findings[0].PrimaryResource.Kind != "NetworkPolicy" {
		t.Fatalf("only the still-unrepaired object belongs in the follow-up: %#v", effect.Request.Findings)
	}
	runToVerification(t, controller, dispatcher, 2)
	for step := int64(5); step <= 9; step++ {
		stepAt(t, controller, step)
	}
	closure, closed := controller.PendingIncidentClosure()
	if !closed || closure.CloseoutGate != nil {
		t.Fatalf("repairs by both responders cover the diff; closed=%v gate=%#v", closed, closure.CloseoutGate)
	}
}

func TestCloseoutGateIsOffByDefaultAndWithoutABaseline(t *testing.T) {
	answer := func(_ int, request IncidentRequest) IncidentResult {
		return repairedResult(request, sdk.ObjectRef{Kind: "Deployment", Name: "geo"})
	}
	off, offDispatcher := newCloseoutController(t, false, 2, []StateChange{isolationPolicy}, answer)
	runToVerification(t, off, offDispatcher, 0)
	for second := int64(2); second <= 4; second++ {
		stepAt(t, off, second)
	}
	closure, closed := off.PendingIncidentClosure()
	if !closed || closure.CloseoutGate != nil {
		t.Fatalf("gate off must keep today's behavior; closed=%v gate=%#v", closed, closure.CloseoutGate)
	}

	none, noneDispatcher := newCloseoutController(t, true, 2, nil, answer)
	none.Baseline = nil
	runToVerification(t, none, noneDispatcher, 0)
	for second := int64(2); second <= 4; second++ {
		stepAt(t, none, second)
	}
	if _, closed := none.PendingIncidentClosure(); !closed {
		t.Fatal("without a baseline there is no diff to gate on")
	}
}

func TestCloseoutGateStaysQuietOnAHealthySingleFaultIncident(t *testing.T) {
	// The repaired object returns to the baseline, so the final diff is empty.
	controller, dispatcher := newCloseoutController(t, true, 2, []StateChange{},
		func(_ int, request IncidentRequest) IncidentResult {
			return repairedResult(request, sdk.ObjectRef{Kind: "ConfigMap", Name: "mongo-rate-script"})
		})
	runToVerification(t, controller, dispatcher, 0)
	for second := int64(2); second <= 4; second++ {
		stepAt(t, controller, second)
	}
	closure, closed := controller.PendingIncidentClosure()
	if !closed || closure.CloseoutGate != nil || dispatcher.attempts != 1 {
		t.Fatalf("an empty diff must close at once; closed=%v gate=%#v attempts=%d", closed, closure.CloseoutGate, dispatcher.attempts)
	}
}

func TestCloseoutGateRejectsAnAcknowledgementWithoutAReason(t *testing.T) {
	result := completedResult("i1")
	result.AcknowledgedStateChanges = []StateChangeAcknowledgement{{Kind: "ConfigMap", Name: "x"}}
	request := IncidentRequest{IncidentID: "i1", SchemaVersion: ProtocolSchemaVersion}
	if err := result.ValidateFor(request); err == nil || !strings.Contains(err.Error(), "acknowledged") {
		t.Fatalf("an acknowledgement needs a reason, got %v", err)
	}
}
