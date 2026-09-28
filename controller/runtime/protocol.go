package runtime

import (
	"fmt"
	"strings"
	"time"

	"sdo.dev/controller/sdk"
)

const ProtocolSchemaVersion = "sdo.dev/v1alpha1"

type DetectorEvaluationStatus string

const (
	DetectorEvaluationFiring DetectorEvaluationStatus = "firing"
	DetectorEvaluationClear  DetectorEvaluationStatus = "clear"
	DetectorEvaluationError  DetectorEvaluationStatus = "error"
)

type IncidentStatus string

const (
	IncidentCompleted IncidentStatus = "completed"
	IncidentFailed    IncidentStatus = "failed"
	IncidentCancelled IncidentStatus = "cancelled"
)

type DetectorEvaluation struct {
	DetectorID   string                   `json:"detector_id"`
	EvaluatedAt  time.Time                `json:"evaluated_at"`
	Status       DetectorEvaluationStatus `json:"status"`
	Fingerprints []string                 `json:"fingerprints"`
	Error        string                   `json:"error,omitempty"`
}

type SurfacedPlaybook struct {
	Path              string                   `json:"path"`
	ParameterBindings map[string]sdk.ObjectRef `json:"parameter_bindings,omitempty"`
}

type PriorOutcomeEvidence struct {
	IncidentID            string   `json:"incident_id"`
	MatchReason           string   `json:"match_reason"`
	RootCauseSummaries    []string `json:"root_cause_summaries"`
	RepairActionSummaries []string `json:"repair_action_summaries"`
	AppliedPlaybooks      []string `json:"applied_playbooks"`
	SourceCommit          string   `json:"source_commit"`
	ExactSourceMatch      bool     `json:"exact_source_match"`
}

type IncidentRequest struct {
	SchemaVersion           string                 `json:"schema_version"`
	Application             string                 `json:"application"`
	Namespace               string                 `json:"namespace"`
	IncidentID              string                 `json:"incident_id"`
	Findings                []sdk.Finding          `json:"findings"`
	DetectorHistory         []DetectorEvaluation   `json:"detector_history"`
	SurfacedPlaybooks       []SurfacedPlaybook     `json:"surfaced_playbooks"`
	RelevantOutcomes        []PriorOutcomeEvidence `json:"relevant_outcomes"`
	SourceCommit            string                 `json:"source_commit"`
	DeployedCommit          string                 `json:"deployed_commit"`
	ArchitectureSummaryPath string                 `json:"architecture_summary_path"`
	HealthObjectivePath     string                 `json:"health_objective_path"`
	RepositoryWorktree      string                 `json:"repository_worktree"`
	RepositoryBaseCommit    string                 `json:"repository_base_commit"`
	ResponseDeadline        time.Time              `json:"response_deadline"`
	CancellationToken       string                 `json:"cancellation_token"`
	RepairPolicy            string                 `json:"repair_policy"`
	// StateChanges is the application's configuration diff against its last
	// healthy baseline when the incident opened; nil without a baseline.
	StateChanges *StateChanges `json:"state_changes,omitempty"`
}

// RootCauseEvidence is one live observation supporting a root cause: a
// detector finding, a synthetic scenario, a state change, or a command and
// its output. Static artifacts are only context.
type RootCauseEvidence struct {
	Kind        string `json:"kind"`
	Source      string `json:"source"`
	Observation string `json:"observation"`
}

type ConfirmedRootCause struct {
	Summary   string          `json:"summary"`
	Resources []sdk.ObjectRef `json:"resources"`
	// Evidence is empty only in results predating evidence-bearing diagnoses.
	Evidence []RootCauseEvidence `json:"evidence,omitempty"`
	// ExplainedDetectors must clear after the fix.
	ExplainedDetectors []string `json:"explained_detectors,omitempty"`
	StaticContext      []string `json:"static_context,omitempty"`
}

type AppliedPlaybook struct {
	Path              string                   `json:"path"`
	ParameterBindings map[string]sdk.ObjectRef `json:"parameter_bindings"`
	Scripts           []string                 `json:"scripts"`
}

type VerificationEvidence struct {
	Name       string    `json:"name"`
	Passed     bool      `json:"passed"`
	Details    string    `json:"details"`
	ObservedAt time.Time `json:"observed_at"`
}

type UsageMetrics struct {
	LLMCalls          int64    `json:"llm_calls"`
	InputTokens       int64    `json:"input_tokens"`
	OutputTokens      int64    `json:"output_tokens"`
	CachedInputTokens int64    `json:"cached_input_tokens"`
	CacheWriteTokens  int64    `json:"cache_write_input_tokens"`
	ReasoningTokens   int64    `json:"reasoning_output_tokens"`
	TotalCostUSD      *float64 `json:"total_cost_usd,omitempty"`
}

type TimingMetrics struct {
	StartedAt   time.Time `json:"started_at"`
	CompletedAt time.Time `json:"completed_at"`
}

type RepairActionReceipt struct {
	ActionID    string    `json:"action_id"`
	Kind        string    `json:"kind"`
	Target      string    `json:"target"`
	Summary     string    `json:"summary"`
	Details     string    `json:"details"`
	StartedAt   time.Time `json:"started_at"`
	CompletedAt time.Time `json:"completed_at"`
	Success     bool      `json:"success"`
	Reversible  bool      `json:"reversible"`
}

type IncidentResult struct {
	SchemaVersion         string                 `json:"schema_version"`
	IncidentID            string                 `json:"incident_id"`
	Status                IncidentStatus         `json:"status"`
	ConfirmedRootCauses   []ConfirmedRootCause   `json:"confirmed_root_causes"`
	AppliedPlaybooks      []AppliedPlaybook      `json:"applied_playbooks"`
	RepairChanges         []string               `json:"repair_changes"`
	RepairActions         []RepairActionReceipt  `json:"repair_actions"`
	FinalDetectorStates   []DetectorEvaluation   `json:"final_detector_states"`
	ProposedMemoryChanges []string               `json:"proposed_memory_changes"`
	VerificationEvidence  []VerificationEvidence `json:"verification_evidence"`
	Usage                 UsageMetrics           `json:"usage"`
	Timing                TimingMetrics          `json:"timing"`
	ResponderSessionID    string                 `json:"responder_session_id,omitempty"`
	Error                 string                 `json:"error,omitempty"`
}

type IncidentClosure struct {
	Request             IncidentRequest      `json:"request"`
	Result              *IncidentResult      `json:"result,omitempty"`
	DispatchError       string               `json:"dispatch_error,omitempty"`
	FinalDetectorStates []DetectorEvaluation `json:"final_detector_states"`
	// IncidentDetectorStates is the latest post-response evaluation of each
	// non-health detector that raised a finding in this incident. It is
	// learning evidence for the broker, not a closure gate.
	IncidentDetectorStates []DetectorEvaluation `json:"incident_detector_states,omitempty"`
	// FinalStateChanges is the configuration diff against the healthy
	// baseline at verification time (N11): a composite fault's later
	// component can land a few seconds after dispatch, while the incident is
	// still open, so it is missing from Request.StateChanges but present
	// here. nil without a baseline. Computed from the in-memory informer
	// cache, so it adds no wall-clock time to closure.
	FinalStateChanges    *StateChanges `json:"final_state_changes,omitempty"`
	DetectedAt           time.Time     `json:"detected_at"`
	DispatchedAt         time.Time     `json:"dispatched_at"`
	ResponderCompletedAt time.Time     `json:"responder_completed_at"`
	VerifiedAt           time.Time     `json:"verified_at"`
	// DetectorReviewRequiredAt is set when health did not clear within the
	// verification window after the responder completed. Health that
	// clears later was not verifiably restored by the responder.
	DetectorReviewRequiredAt *time.Time `json:"detector_review_required_at,omitempty"`
	DetectorReviewReason     string     `json:"detector_review_reason,omitempty"`
	// CleanedHelpers are the responder helper objects the controller
	// deleted after the responder completed, as Kind/namespace/name.
	CleanedHelpers []string `json:"cleaned_helpers,omitempty"`
}

func (request IncidentRequest) Validate() error {
	if request.SchemaVersion != ProtocolSchemaVersion {
		return fmt.Errorf("unsupported schema version %q", request.SchemaVersion)
	}
	if strings.TrimSpace(request.Application) == "" || strings.TrimSpace(request.Namespace) == "" {
		return fmt.Errorf("application and namespace are required")
	}
	if strings.TrimSpace(request.IncidentID) == "" {
		return fmt.Errorf("incident id is required")
	}
	if len(request.Findings) == 0 || len(request.DetectorHistory) == 0 {
		return fmt.Errorf("findings and detector history are required")
	}
	if request.ResponseDeadline.IsZero() || strings.TrimSpace(request.CancellationToken) == "" {
		return fmt.Errorf("response deadline and cancellation token are required")
	}
	if strings.TrimSpace(request.RepositoryWorktree) == "" || strings.TrimSpace(request.RepositoryBaseCommit) == "" {
		return fmt.Errorf("repository worktree and base commit are required")
	}
	if request.RepairPolicy != "commit" && request.RepairPolicy != "recorded-actions" {
		return fmt.Errorf("unsupported repair policy %q", request.RepairPolicy)
	}
	return nil
}

func (result IncidentResult) ValidateFor(request IncidentRequest) error {
	if result.SchemaVersion != ProtocolSchemaVersion {
		return fmt.Errorf("unsupported schema version %q", result.SchemaVersion)
	}
	if result.IncidentID != request.IncidentID {
		return fmt.Errorf("incident id %q does not match request %q", result.IncidentID, request.IncidentID)
	}
	if result.Status != IncidentCompleted && result.Status != IncidentFailed && result.Status != IncidentCancelled {
		return fmt.Errorf("unsupported incident status %q", result.Status)
	}
	if result.Timing.StartedAt.IsZero() || result.Timing.CompletedAt.IsZero() {
		return fmt.Errorf("timing is required")
	}
	if result.Timing.CompletedAt.Before(result.Timing.StartedAt) {
		return fmt.Errorf("completion time is before start time")
	}
	actionIDs := make(map[string]struct{}, len(result.RepairActions))
	for _, action := range result.RepairActions {
		if strings.TrimSpace(action.ActionID) == "" || strings.TrimSpace(action.Kind) == "" ||
			strings.TrimSpace(action.Target) == "" || strings.TrimSpace(action.Summary) == "" ||
			strings.TrimSpace(action.Details) == "" {
			return fmt.Errorf("repair action identity, kind, target, summary, and details are required")
		}
		if action.StartedAt.IsZero() || action.CompletedAt.IsZero() || action.CompletedAt.Before(action.StartedAt) {
			return fmt.Errorf("repair action timing is invalid")
		}
		if _, exists := actionIDs[action.ActionID]; exists {
			return fmt.Errorf("repair action id %q is duplicated", action.ActionID)
		}
		actionIDs[action.ActionID] = struct{}{}
	}
	return nil
}
