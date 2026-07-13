package controller

import (
	"context"
	"fmt"
)

type WorkspaceEffect struct {
	IncidentID string `json:"incident_id"`
}

type ClosureEffect struct {
	Closure IncidentClosure `json:"closure"`
}

type ClosureAcknowledgmentEffect struct {
	Receipt ClosureReceipt `json:"receipt"`
}

func (c *Controller) PendingWorkspaceEffect() (WorkspaceEffect, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.broker == nil || c.dispatchState != "workspace_pending" || c.currentIncidentRequest == nil {
		return WorkspaceEffect{}, false
	}
	return WorkspaceEffect{IncidentID: c.currentIncidentRequest.IncidentID}, true
}

func (c *Controller) ExecuteWorkspaceEffect(ctx context.Context, effect WorkspaceEffect) error {
	actionCtx, cancel, err := c.actionContext(ctx)
	if err != nil {
		return err
	}
	c.mu.Lock()
	if c.broker == nil || c.dispatchState != "workspace_pending" || c.currentIncidentRequest == nil ||
		c.currentIncidentRequest.IncidentID != effect.IncidentID {
		c.mu.Unlock()
		cancel()
		return fmt.Errorf("workspace effect is not pending")
	}
	broker := c.broker
	c.dispatchState = "workspace_running"
	c.mu.Unlock()
	go func() {
		defer cancel()
		workspace, prepareErr := broker.PrepareIncident(actionCtx, effect.IncidentID)
		c.workspaceResults <- workspaceCompletion{workspace: workspace, err: prepareErr}
	}()
	return nil
}

func (c *Controller) PendingClosureEffect() (ClosureEffect, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.broker == nil || c.closureState != "pending" || c.pendingClosure == nil {
		return ClosureEffect{}, false
	}
	return ClosureEffect{Closure: *cloneIncidentClosure(c.pendingClosure)}, true
}

func (c *Controller) ExecuteClosureEffect(ctx context.Context, effect ClosureEffect) error {
	actionCtx, cancel, err := c.actionContext(ctx)
	if err != nil {
		return err
	}
	c.mu.Lock()
	if c.broker == nil || c.closureState != "pending" || c.pendingClosure == nil ||
		c.pendingClosure.Request.IncidentID != effect.Closure.Request.IncidentID {
		c.mu.Unlock()
		cancel()
		return fmt.Errorf("closure effect is not pending")
	}
	broker := c.broker
	c.closureState = "processing"
	c.mu.Unlock()
	go func() {
		defer cancel()
		receipt, processErr := broker.ProcessClosure(actionCtx, effect.Closure)
		c.closureResults <- closureCompletion{receipt: receipt, err: processErr}
	}()
	return nil
}

func (c *Controller) PendingClosureAcknowledgmentEffect() (ClosureAcknowledgmentEffect, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.broker == nil || c.closureState != "committed" || c.closureReceipt == nil {
		return ClosureAcknowledgmentEffect{}, false
	}
	return ClosureAcknowledgmentEffect{Receipt: *cloneClosureReceipt(c.closureReceipt)}, true
}

func (c *Controller) ExecuteClosureAcknowledgmentEffect(
	ctx context.Context,
	effect ClosureAcknowledgmentEffect,
) error {
	actionCtx, cancel, err := c.actionContext(ctx)
	if err != nil {
		return err
	}
	c.mu.Lock()
	if c.broker == nil || c.closureState != "committed" || c.closureReceipt == nil ||
		c.closureReceipt.AckToken != effect.Receipt.AckToken {
		c.mu.Unlock()
		cancel()
		return fmt.Errorf("closure acknowledgment effect is not pending")
	}
	broker := c.broker
	c.closureState = "acknowledging"
	c.mu.Unlock()
	go func() {
		defer cancel()
		ackErr := broker.AcknowledgeClosure(actionCtx, effect.Receipt)
		c.acknowledgmentResults <- acknowledgmentCompletion{err: ackErr}
	}()
	return nil
}

func (c *Controller) processBrokerCompletions() {
	for {
		select {
		case completion := <-c.workspaceResults:
			c.handleWorkspaceCompletion(completion)
		default:
			goto closures
		}
	}

closures:
	for {
		select {
		case completion := <-c.closureResults:
			c.handleClosureCompletion(completion)
		default:
			goto acknowledgments
		}
	}

acknowledgments:
	for {
		select {
		case completion := <-c.acknowledgmentResults:
			c.handleAcknowledgmentCompletion(completion)
		default:
			return
		}
	}
}

func (c *Controller) handleWorkspaceCompletion(completion workspaceCompletion) {
	c.mu.Lock()
	if completion.err != nil {
		c.dispatchState = "workspace_pending"
		c.mu.Unlock()
		c.reportBrokerError("prepare incident worktree", completion.err)
		return
	}
	if c.currentIncidentRequest == nil || completion.workspace.IncidentID != c.currentIncidentRequest.IncidentID ||
		completion.workspace.Worktree == "" || completion.workspace.BaseCommit == "" {
		c.dispatchState = "workspace_pending"
		c.mu.Unlock()
		c.reportBrokerError("prepare incident worktree", fmt.Errorf("broker returned invalid workspace"))
		return
	}
	c.currentIncidentRequest.RepositoryWorktree = completion.workspace.Worktree
	c.currentIncidentRequest.RepositoryBaseCommit = completion.workspace.BaseCommit
	c.dispatchState = "pending"
	c.mu.Unlock()
}

func (c *Controller) handleClosureCompletion(completion closureCompletion) {
	c.mu.Lock()
	if completion.err != nil {
		c.closureState = "pending"
		c.mu.Unlock()
		c.reportBrokerError("process incident closure", completion.err)
		return
	}
	if c.pendingClosure == nil || completion.receipt.IncidentID != c.pendingClosure.Request.IncidentID ||
		completion.receipt.OutcomeCommit == "" || completion.receipt.AckToken == "" {
		c.closureState = "pending"
		c.mu.Unlock()
		c.reportBrokerError("process incident closure", fmt.Errorf("broker returned invalid closure receipt"))
		return
	}
	c.closureReceipt = cloneClosureReceipt(&completion.receipt)
	c.closureState = "committed"
	c.mu.Unlock()
}

func (c *Controller) handleAcknowledgmentCompletion(completion acknowledgmentCompletion) {
	c.mu.Lock()
	if completion.err != nil {
		c.closureState = "committed"
		c.mu.Unlock()
		c.reportBrokerError("acknowledge incident closure", completion.err)
		return
	}
	if c.closureReceipt != nil {
		c.lastAcknowledgedIncidentID = c.closureReceipt.IncidentID
	}
	c.pendingClosure = nil
	c.closureReceipt = nil
	c.closureState = ""
	// Any activation buffered before or during this incident is represented by
	// the acknowledged closure. Drop it so crash restoration cannot redispatch
	// the same evidence; a genuinely recurring fault must clear and reactivate.
	c.batcher.Drain()
	c.mu.Unlock()
}

func (c *Controller) reportBrokerError(operation string, err error) {
	if c.OnError != nil {
		c.OnError(fmt.Errorf("%s: %w", operation, err))
	}
}
