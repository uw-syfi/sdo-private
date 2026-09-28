package runtime

import (
	"sort"
	"time"

	"sdo.dev/controller/sdk"
)

type FindingState struct {
	DetectorID  string      `json:"detector_id"`
	Finding     sdk.Finding `json:"finding"`
	FiringCount int         `json:"firing_count"`
	ClearCount  int         `json:"clear_count"`
	Active      bool        `json:"active"`
	// FirstSeenAt is when this finding was first observed since it last
	// cleared. It anchors a policy's MinDuration: the finding may not
	// activate until now Sub FirstSeenAt is at least that long.
	FirstSeenAt time.Time `json:"first_seen_at,omitempty"`
}

type FindingChanges struct {
	Activated []sdk.Finding
	Cleared   []string
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

func (t *FindingStateTracker) SetPolicy(detectorID string, firingThreshold int, clearThreshold int, minDuration time.Duration) {
	if firingThreshold < 1 || clearThreshold < 1 || minDuration < 0 {
		return
	}
	t.policies[detectorID] = sdk.PersistencePolicy{Firing: firingThreshold, Clearing: clearThreshold, MinDuration: minDuration}
}

func (t *FindingStateTracker) policy(detectorID string) sdk.PersistencePolicy {
	if policy, ok := t.policies[detectorID]; ok {
		return policy
	}
	return sdk.PersistencePolicy{Firing: t.firingThreshold, Clearing: t.clearThreshold}
}

func (t *FindingStateTracker) Observe(now time.Time, detectorID string, findings []sdk.Finding) FindingChanges {
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
	for fingerprint, finding := range seen {
		key := FindingStateKey(detectorID, fingerprint)
		state, exists := t.states[key]
		if !exists {
			state = &FindingState{DetectorID: detectorID}
			t.states[key] = state
		}
		if state.FiringCount == 0 {
			state.FirstSeenAt = now
		}
		state.Finding = finding
		state.ClearCount = 0
		state.FiringCount++
		if !state.Active && state.FiringCount >= policy.Firing && persistedLongEnough(policy, state.FirstSeenAt, now) {
			state.Active = true
			changes.Activated = append(changes.Activated, finding)
		}
	}

	for key, state := range t.states {
		if state.DetectorID != detectorID {
			continue
		}
		if _, ok := seen[FindingFingerprint(state.Finding)]; ok {
			continue
		}
		state.FiringCount = 0
		if !state.Active {
			continue
		}
		state.ClearCount++
		if state.ClearCount >= policy.Clearing {
			state.Active = false
			state.ClearCount = 0
			changes.Cleared = append(changes.Cleared, key)
		}
	}
	sort.Slice(changes.Activated, func(left int, right int) bool {
		return changes.Activated[left].Fingerprint < changes.Activated[right].Fingerprint
	})
	sort.Strings(changes.Cleared)
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

// Quiet reports that no finding is active or pending confirmation.
func (t *FindingStateTracker) Quiet() bool {
	for _, state := range t.states {
		if state.Active || state.FiringCount > 0 {
			return false
		}
	}
	return true
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

// persistedLongEnough reports whether a finding first seen at firstSeenAt has
// been observed continuously for at least policy.MinDuration as of now. A
// zero MinDuration keeps the evaluation-count-only policy.
func persistedLongEnough(policy sdk.PersistencePolicy, firstSeenAt time.Time, now time.Time) bool {
	if policy.MinDuration <= 0 {
		return true
	}
	return now.Sub(firstSeenAt) >= policy.MinDuration
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
