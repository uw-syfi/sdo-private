package runtime

import "context"

type IncidentWorkspace struct {
	IncidentID string `json:"incident_id"`
	Worktree   string `json:"worktree"`
	BaseCommit string `json:"base_commit"`
}

type ClosureReceipt struct {
	IncidentID       string `json:"incident_id"`
	Worktree         string `json:"worktree"`
	BaseCommit       string `json:"base_commit"`
	ProposalCommit   string `json:"proposal_commit,omitempty"`
	OutcomeCommit    string `json:"outcome_commit"`
	ReflectionCommit string `json:"reflection_commit,omitempty"`
	AckToken         string `json:"ack_token"`
}

type IncidentBroker interface {
	PrepareIncident(context.Context, string) (IncidentWorkspace, error)
	ProcessClosure(context.Context, IncidentClosure) (ClosureReceipt, error)
	AcknowledgeClosure(context.Context, ClosureReceipt) error
	// ReleaseIncident reaps the worktree of an incident that was prepared and
	// then superseded or cancelled before any closure, so the only other
	// cleanup path (AcknowledgeClosure) never ran. It must be a no-op for an
	// incident that is closing or already acknowledged, and idempotent.
	ReleaseIncident(context.Context, string) error
}

type workspaceCompletion struct {
	workspace IncidentWorkspace
	err       error
}

type closureCompletion struct {
	receipt ClosureReceipt
	err     error
}

type acknowledgmentCompletion struct {
	err error
}

type releaseCompletion struct {
	incidentID string
	err        error
}
