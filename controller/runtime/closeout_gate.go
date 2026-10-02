package runtime

import (
	"fmt"
	"sort"
	"strings"
	"time"

	"sdo.dev/controller/sdk"
)

// The close-out gate keeps an incident open while the application still
// differs from its healthy baseline in an object that no repair touched.
// Health detectors judge behavior; this judges the diff the responder was
// shown. It is generic on purpose: it names no kind and no fault, only
// "changed since healthy, and nobody repaired or explained it". The diff only
// covers configuration kinds, so objects that applications churn on their own
// (pods, ReplicaSets, endpoints) never trip it, and the controller's own
// objects are excluded by the tracker.

// closeoutKey normalizes an object reference so a receipt's "networkpolicy" or
// "NetworkPolicies" matches the diff's "NetworkPolicy".
func closeoutKey(kind string, name string) string {
	kind = strings.ToLower(strings.TrimSpace(kind))
	switch {
	case strings.HasSuffix(kind, "ies"):
		kind = strings.TrimSuffix(kind, "ies") + "y"
	case strings.HasSuffix(kind, "s") && !strings.HasSuffix(kind, "ss"):
		kind = strings.TrimSuffix(kind, "s")
	}
	return kind + "/" + strings.TrimSpace(name)
}

// recordCloseoutEvidenceLocked folds a completed responder's repairs and
// acknowledgements into the incident-wide sets. Failed repair actions touch
// nothing. Called with c.mu held.
func (c *Controller) recordCloseoutEvidenceLocked(result IncidentResult) {
	if !c.config.CloseoutStateGate {
		return
	}
	if c.incidentRepaired == nil {
		c.incidentRepaired = make(map[string]struct{})
	}
	if c.incidentAcknowledged == nil {
		c.incidentAcknowledged = make(map[string]string)
	}
	for _, action := range result.RepairActions {
		if !action.Success {
			continue
		}
		for _, resource := range action.Resources {
			c.incidentRepaired[closeoutKey(resource.Kind, resource.Name)] = struct{}{}
		}
	}
	for _, acknowledged := range result.AcknowledgedStateChanges {
		c.incidentAcknowledged[closeoutKey(acknowledged.Kind, acknowledged.Name)] = acknowledged.Reason
	}
}

// closeoutGateLocked applies the gate to the diff at verification time. It
// returns the outcome to record on the closure, or sendBack when it has opened
// a follow-up (or is waiting for the follow-up cooldown) instead. Called with
// c.mu held.
func (c *Controller) closeoutGateLocked(final *StateChanges, now time.Time) (*CloseoutGateOutcome, bool) {
	if !c.config.CloseoutStateGate || final == nil {
		return nil, false
	}
	var unrepaired []StateChange
	var acknowledged []CloseoutGateObject
	for _, change := range final.Changes {
		key := closeoutKey(change.Kind, change.Name)
		if _, repaired := c.incidentRepaired[key]; repaired {
			continue
		}
		if reason, ok := c.incidentAcknowledged[key]; ok {
			acknowledged = append(acknowledged, CloseoutGateObject{Kind: change.Kind, Name: change.Name, Reason: reason})
			continue
		}
		unrepaired = append(unrepaired, change)
	}
	if len(unrepaired) == 0 {
		if len(acknowledged) == 0 {
			return nil, false
		}
		return &CloseoutGateOutcome{Outcome: CloseoutAcknowledged, Objects: acknowledged}, false
	}
	if c.followUpsRemainLocked() {
		if now.Before(c.followUpDueAtLocked()) {
			return nil, true
		}
		findings := closeoutFindings(unrepaired, c.config.Namespace)
		note := " The close-out gate found objects still different from the healthy baseline that no repair " +
			"touched and no responder explained: " + closeoutObjectList(unrepaired) + "."
		if c.openFollowUpLocked(now, findings, note, final) {
			return nil, true
		}
	}
	objects := make([]CloseoutGateObject, 0, len(unrepaired)+len(acknowledged))
	for _, change := range unrepaired {
		objects = append(objects, CloseoutGateObject{Kind: change.Kind, Name: change.Name})
	}
	objects = append(objects, acknowledged...)
	return &CloseoutGateOutcome{Outcome: CloseoutExhausted, Objects: objects}, false
}

func closeoutObjectList(changes []StateChange) string {
	parts := make([]string, 0, len(changes))
	for _, change := range changes {
		parts = append(parts, fmt.Sprintf("%s/%s (%s)", change.Kind, change.Name, change.Change))
	}
	sort.Strings(parts)
	return strings.Join(parts, ", ")
}

// closeoutFindings renders unrepaired diff objects as findings for a
// follow-up request. They come from the controller, not from a detector, so
// they carry no playbook and are never tracked as a health finding.
func closeoutFindings(changes []StateChange, namespace string) []sdk.Finding {
	findings := make([]sdk.Finding, 0, len(changes))
	for _, change := range changes {
		fields := make([]string, 0, len(change.Fields))
		for _, field := range change.Fields {
			fields = append(fields, field.Field)
		}
		evidence := fmt.Sprintf("%s/%s is %s relative to the last healthy baseline", change.Kind, change.Name, change.Change)
		if len(fields) > 0 {
			evidence += " (fields: " + strings.Join(fields, ", ") + ")"
		}
		findings = append(findings, sdk.Finding{
			DetectorID: CloseoutGateDetectorID, RuleID: CloseoutGateRuleID,
			Status: sdk.FindingActive, Severity: sdk.SeverityWarning,
			Summary: fmt.Sprintf(
				"%s/%s still differs from the healthy baseline and no repair touched it", change.Kind, change.Name,
			),
			Evidence:        evidence + "; repair it, or acknowledge it with a reason if it is unrelated",
			PrimaryResource: sdk.ObjectRef{Kind: change.Kind, Namespace: namespace, Name: change.Name},
			Fingerprint:     "closeout/" + closeoutKey(change.Kind, change.Name),
		})
	}
	return findings
}
