package runtime

import (
	"sort"

	"sdo.dev/controller/sdk"
)

type FindingState struct {
	DetectorID  string      `json:"detector_id"`
	Finding     sdk.Finding `json:"finding"`
	FiringCount int         `json:"firing_count"`
	ClearCount  int         `json:"clear_count"`
	Active      bool        `json:"active"`
	// Activations, Clears, and NotPersisted count this finding's completed
	// transitions. They make a telemetry event id a pure function of durable
	// state, so a replay after a crash reproduces the ids it already emitted.
	Activations  int `json:"activations,omitempty"`
	Clears       int `json:"clears,omitempty"`
	NotPersisted int `json:"not_persisted,omitempty"`
}

// FindingTransition is one firing-state change produced by Observe.
type FindingTransition struct {
	Event       FiringEvent
	DetectorID  string
	Finding     sdk.Finding
	FiringCount int
	ClearCount  int
	// Sequence is the 1-based ordinal of this transition kind for the finding.
	Sequence int
}

type FindingChanges struct {
	Activated []sdk.Finding
	Cleared   []string
	// Transitions lists activations, clears, and findings that vanished before
	// reaching their firing threshold, in deterministic order.
	Transitions []FindingTransition
}

type FindingStateTracker struct {
	firingThreshold int
	clearThreshold  int
	policies        map[string]sdk.PersistencePolicy
	states          map[string]*FindingState
}

func NewFindingStateTracker(firingThreshold int, clearThreshold int) *FindingStateTracker {
	return &FindingStateTracker{
		firingThreshold: firingThreshold,
		clearThreshold:  clearThreshold,
		policies:        make(map[string]sdk.PersistencePolicy),
		states:          make(map[string]*FindingState),
	}
}

func (t *FindingStateTracker) SetPolicy(detectorID string, firingThreshold int, clearThreshold int) {
	if firingThreshold < 1 || clearThreshold < 1 {
		return
	}
	t.policies[detectorID] = sdk.PersistencePolicy{Firing: firingThreshold, Clearing: clearThreshold}
}

func (t *FindingStateTracker) policy(detectorID string) sdk.PersistencePolicy {
	if policy, ok := t.policies[detectorID]; ok {
		return policy
	}
	return sdk.PersistencePolicy{Firing: t.firingThreshold, Clearing: t.clearThreshold}
}

func (t *FindingStateTracker) Observe(detectorID string, findings []sdk.Finding) FindingChanges {
	policy := t.policy(detectorID)
	seen := make(map[string]sdk.Finding)
	for _, finding := range findings {
		if finding.Status == sdk.FindingResolved {
			continue
		}
		fingerprint := FindingFingerprint(finding)
		finding.Fingerprint = fingerprint
		seen[fingerprint] = finding
	}

	changes := FindingChanges{}
	activated := make(map[string]FindingTransition)
	for fingerprint, finding := range seen {
		key := FindingStateKey(detectorID, fingerprint)
		state, exists := t.states[key]
		if !exists {
			state = &FindingState{DetectorID: detectorID}
			t.states[key] = state
		}
		state.Finding = finding
		state.ClearCount = 0
		state.FiringCount++
		if !state.Active && state.FiringCount >= policy.Firing {
			state.Active = true
			state.Activations++
			changes.Activated = append(changes.Activated, finding)
			activated[fingerprint] = FindingTransition{
				Event: FiringActivated, DetectorID: detectorID, Finding: finding,
				FiringCount: state.FiringCount, Sequence: state.Activations,
			}
		}
	}

	var cleared, notPersisted []FindingTransition
	for key, state := range t.states {
		if state.DetectorID != detectorID {
			continue
		}
		if _, ok := seen[FindingFingerprint(state.Finding)]; ok {
			continue
		}
		droppedFiring := state.FiringCount
		state.FiringCount = 0
		if !state.Active {
			if droppedFiring > 0 {
				state.NotPersisted++
				notPersisted = append(notPersisted, FindingTransition{
					Event: FiringNotPersisted, DetectorID: detectorID, Finding: state.Finding,
					FiringCount: droppedFiring, Sequence: state.NotPersisted,
				})
			}
			continue
		}
		state.ClearCount++
		if state.ClearCount >= policy.Clearing {
			state.Clears++
			cleared = append(cleared, FindingTransition{
				Event: FiringCleared, DetectorID: detectorID, Finding: state.Finding,
				ClearCount: state.ClearCount, Sequence: state.Clears,
			})
			state.Active = false
			state.ClearCount = 0
			changes.Cleared = append(changes.Cleared, key)
		}
	}
	sort.Slice(changes.Activated, func(left int, right int) bool {
		return changes.Activated[left].Fingerprint < changes.Activated[right].Fingerprint
	})
	sort.Strings(changes.Cleared)
	for _, finding := range changes.Activated {
		changes.Transitions = append(changes.Transitions, activated[finding.Fingerprint])
	}
	byFingerprint := func(list []FindingTransition) {
		sort.Slice(list, func(left int, right int) bool {
			return FindingFingerprint(list[left].Finding) < FindingFingerprint(list[right].Finding)
		})
	}
	byFingerprint(cleared)
	byFingerprint(notPersisted)
	changes.Transitions = append(changes.Transitions, cleared...)
	changes.Transitions = append(changes.Transitions, notPersisted...)
	return changes
}

func (t *FindingStateTracker) HasActive() bool {
	for _, state := range t.states {
		if state.Active {
			return true
		}
	}
	return false
}

func (t *FindingStateTracker) HasActiveKeys(keys []string) bool {
	for _, key := range keys {
		if state, ok := t.states[key]; ok && state.Active {
			return true
		}
	}
	return false
}

func (t *FindingStateTracker) HasActiveDetector(detectorID string) bool {
	for _, state := range t.states {
		if state.DetectorID == detectorID && state.Active {
			return true
		}
	}
	return false
}

// HasPendingDetector reports whether a detector has observed a finding that
// has not yet reached its firing threshold.
func (t *FindingStateTracker) HasPendingDetector(detectorID string) bool {
	for _, state := range t.states {
		if state.DetectorID == detectorID && !state.Active && state.FiringCount > 0 {
			return true
		}
	}
	return false
}

func (t *FindingStateTracker) Snapshot() map[string]FindingState {
	result := make(map[string]FindingState, len(t.states))
	for fingerprint, state := range t.states {
		result[fingerprint] = *state
	}
	return result
}

func (t *FindingStateTracker) Restore(states map[string]FindingState) {
	t.states = make(map[string]*FindingState, len(states))
	for fingerprint, state := range states {
		copy := state
		t.states[fingerprint] = &copy
	}
}

func FindingFingerprint(finding sdk.Finding) string {
	if finding.Fingerprint != "" {
		return finding.Fingerprint
	}
	resource := finding.PrimaryResource
	return finding.DetectorID + "/" + finding.RuleID + "/" + resource.Namespace + "/" + resource.Kind + "/" + resource.Name
}

func FindingStateKey(detectorID string, fingerprint string) string {
	return detectorID + "\x00" + fingerprint
}
