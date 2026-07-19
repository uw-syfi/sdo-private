package controller

import (
	"context"
	"fmt"
)

type DispatchEffect struct {
	Request IncidentRequest `json:"request"`
}

func (c *Controller) PendingDispatchEffect() (DispatchEffect, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.dispatchState != "pending" || c.currentIncidentRequest == nil {
		return DispatchEffect{}, false
	}
	return DispatchEffect{Request: *cloneIncidentRequest(c.currentIncidentRequest)}, true
}

func (c *Controller) ExecuteDispatchEffect(ctx context.Context, effect DispatchEffect) error {
	actionCtx, cancel, err := c.actionContext(ctx)
	if err != nil {
		return err
	}
	c.mu.Lock()
	if c.dispatchState != "pending" || c.currentIncidentRequest == nil {
		c.mu.Unlock()
		cancel()
		return fmt.Errorf("dispatch effect is not pending")
	}
	if effect.Request.IncidentID != c.currentIncidentRequest.IncidentID {
		c.mu.Unlock()
		cancel()
		return fmt.Errorf("dispatch effect incident %q does not match pending incident %q", effect.Request.IncidentID, c.currentIncidentRequest.IncidentID)
	}
	c.dispatchState = "running"
	if c.incidentDispatchedAt.IsZero() {
		c.incidentDispatchedAt = c.incidentDetectedAt
	}
	c.mu.Unlock()
	go func() {
		defer cancel()
		result, dispatchErr := c.dispatcher.Dispatch(actionCtx, effect.Request)
		c.results <- dispatchCompletion{incidentID: effect.Request.IncidentID, result: result, err: dispatchErr}
	}()
	return nil
}
