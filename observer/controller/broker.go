package controller

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
