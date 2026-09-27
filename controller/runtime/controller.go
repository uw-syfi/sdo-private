package runtime

import (
	"context"
	"fmt"
	"sort"
	"strings"
	"sync"
	"time"

	"sdo.dev/controller/core"
	"sdo.dev/controller/sdk"
)

type SnapshotProvider interface {
	Snapshot(context.Context) (sdk.DetectionContext, error)
}

type Dispatcher interface {
	Dispatch(context.Context, IncidentRequest) (IncidentResult, error)
}

type ControllerConfig struct {
	Application             string
	Namespace               string
	SourceCommit            string
	DeployedCommit          string
	ArchitectureSummaryPath string
	HealthObjectivePath     string
	RepositoryWorktree      string
	ResponseTimeout         time.Duration
	VerificationTimeout     time.Duration
	FiringThreshold         int
	ClearThreshold          int
	BatchDebounce           time.Duration
	RepairPolicy            string
}

type dispatchCompletion struct {
	incidentID string
	result     IncidentResult
	err        error
}

type Controller struct {
	config        ControllerConfig
	scheduler     *Scheduler
	tracker       *FindingStateTracker
	batcher       *Batcher
	provider      SnapshotProvider
	dispatcher    Dispatcher
	history       []DetectorEvaluation
	results       chan dispatchCompletion
	stateStore    StateStore
	stateRevision string

	mu                         sync.Mutex
	incidentOpen               bool
	responderDone              bool
	currentIncidentRequest     *IncidentRequest
	dispatchState              string
	incidentFindingKeys        []string
	healthDetectorIDs          []string
	detectorSpecs              map[string]sdk.DetectorSpec
	currentIncidentResult      *IncidentResult
	dispatchError              string
	incidentDetectedAt         time.Time
	incidentDispatchedAt       time.Time
	responderCompletedAt       time.Time
	detectorReviewRequired     bool
	detectorReviewRequiredAt   time.Time
	detectorReviewReason       string
	pendingClosure             *IncidentClosure
	closureState               string
	closureReceipt             *ClosureReceipt
	lastAcknowledgedIncidentID string
	broker                     IncidentBroker
	workspaceResults           chan workspaceCompletion
	closureResults             chan closureCompletion
	acknowledgmentResults      chan acknowledgmentCompletion

	OnError          func(error)
	OnResult         func(IncidentResult)
	OnEvaluation     func([]sdk.Finding)
	OnIncidentClosed func(IncidentClosure)
	CanAct           func() bool
	GuardAction      func(context.Context) (context.Context, context.CancelFunc, error)
}

func NewController(
	config ControllerConfig,
	detectors []sdk.Detector,
	provider SnapshotProvider,
	dispatcher Dispatcher,
	start time.Time,
) (*Controller, error) {
	if provider == nil {
		return nil, fmt.Errorf("snapshot provider is required")
	}
	if dispatcher == nil {
		return nil, fmt.Errorf("dispatcher is required")
	}
	if config.FiringThreshold < 1 || config.ClearThreshold < 1 {
		return nil, fmt.Errorf("firing and clear thresholds must be positive")
	}
	if config.ResponseTimeout <= 0 {
		return nil, fmt.Errorf("response timeout must be positive")
	}
	if config.VerificationTimeout < 0 {
		return nil, fmt.Errorf("verification timeout must not be negative")
	}
	if config.VerificationTimeout == 0 {
		config.VerificationTimeout = config.ResponseTimeout
	}
	if config.RepairPolicy == "" {
		config.RepairPolicy = "commit"
	}
	if config.RepairPolicy != "commit" && config.RepairPolicy != "recorded-actions" {
		return nil, fmt.Errorf("unsupported repair policy %q", config.RepairPolicy)
	}
	if err := core.ValidateDetectors(detectors); err != nil {
		return nil, err
	}
	healthDetectorIDs := make([]string, 0)
	detectorSpecs := make(map[string]sdk.DetectorSpec, len(detectors))
	tracker := NewFindingStateTracker(config.FiringThreshold, config.ClearThreshold)
	for _, detector := range detectors {
		spec := detector.Spec()
		detectorSpecs[spec.ID] = spec
		if spec.Persistence.Firing > 0 && spec.Persistence.Clearing > 0 {
			tracker.SetPolicy(spec.ID, spec.Persistence.Firing, spec.Persistence.Clearing)
		}
		if spec.Class == sdk.DetectorClassHealth {
			healthDetectorIDs = append(healthDetectorIDs, spec.ID)
		}
	}
	sort.Strings(healthDetectorIDs)
	return &Controller{
		config: config, scheduler: NewScheduler(detectors, start),
		tracker: tracker,
		batcher: NewDebouncedBatcher(config.BatchDebounce), provider: provider, dispatcher: dispatcher,
		results: make(chan dispatchCompletion, 1), healthDetectorIDs: healthDetectorIDs,
		detectorSpecs:    detectorSpecs,
		workspaceResults: make(chan workspaceCompletion, 1), closureResults: make(chan closureCompletion, 1),
		acknowledgmentResults: make(chan acknowledgmentCompletion, 1),
	}, nil
}

func (c *Controller) Step(ctx context.Context, now time.Time, event *sdk.WatchKind) error {
	c.processDispatchCompletions(now)
	c.processBrokerCompletions()
	detectors := c.scheduler.Select(now, event)
	if len(detectors) == 0 {
		c.maybeCloseIncident(now)
		return c.dispatchReady(ctx, now)
	}
	snapshot, err := c.provider.Snapshot(ctx)
	if err != nil {
		return fmt.Errorf("create detection snapshot: %w", err)
	}

	sampleFindings := make([]sdk.Finding, 0)
	for _, detector := range detectors {
		spec := detector.Spec()
		findings, detectErr := detector.Detect(ctx, snapshot)
		if detectErr != nil {
			c.recordDetectorError(spec.ID, now, detectErr)
			continue
		}
		validFindings := make([]sdk.Finding, 0, len(findings))
		valid := true
		for _, finding := range findings {
			if finding.DetectorID == "" {
				finding.DetectorID = spec.ID
			}
			if len(finding.Playbooks) == 0 && len(spec.Playbooks) > 0 {
				finding.Playbooks = append([]string(nil), spec.Playbooks...)
			}
			finding.Fingerprint = FindingFingerprint(finding)
			if validationErr := core.ValidateFinding(spec, finding); validationErr != nil {
				c.recordDetectorError(spec.ID, now, validationErr)
				valid = false
				break
			}
			validFindings = append(validFindings, finding)
		}
		if !valid {
			continue
		}
		changes := c.tracker.Observe(spec.ID, validFindings)
		sampleFindings = append(sampleFindings, validFindings...)
		c.batcher.RemoveKeys(changes.Cleared)
		for _, finding := range changes.Activated {
			if c.mergeIntoOpenIncident(finding) {
				continue
			}
			batchSpec := c.detectorSpecs[spec.ID]
			if batchSpec.Class != "" && !severityAtLeast(finding.Severity, batchSpec.Batching.Severity) {
				continue
			}
			if batchSpec.Class == "" {
				c.batcher.AddAt(finding, now)
			} else {
				c.batcher.AddAtWithDebounce(finding, now, batchSpec.Batching.Debounce)
			}
		}
		c.recordEvaluation(spec.ID, now, validFindings)
	}
	sort.Slice(sampleFindings, func(left int, right int) bool {
		return sampleFindings[left].Fingerprint < sampleFindings[right].Fingerprint
	})
	if c.OnEvaluation != nil {
		c.OnEvaluation(sampleFindings)
	}

	if c.IncidentOpen() {
		c.maybeCloseIncident(now)
		if c.IncidentOpen() {
			return nil
		}
		return c.dispatchReady(ctx, now)
	}
	return c.dispatchReady(ctx, now)
}

func (c *Controller) mergeIntoOpenIncident(finding sdk.Finding) bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	if !c.incidentOpen || c.currentIncidentRequest == nil {
		return false
	}
	key := FindingStateKey(finding.DetectorID, FindingFingerprint(finding))
	finding.Fingerprint = FindingFingerprint(finding)
	for index, existing := range c.currentIncidentRequest.Findings {
		if FindingStateKey(existing.DetectorID, FindingFingerprint(existing)) == key {
			c.currentIncidentRequest.Findings[index] = finding
			sortFindings(c.currentIncidentRequest.Findings)
			return true
		}
	}
	return false
}

func (c *Controller) dispatchReady(ctx context.Context, now time.Time) error {
	c.mu.Lock()
	incidentOpen := c.incidentOpen
	closurePending := c.pendingClosure != nil && c.broker != nil
	c.mu.Unlock()
	if incidentOpen || closurePending {
		return nil
	}
	if !c.batcher.Ready(now) {
		return nil
	}
	batch := c.batcher.DrainReady(now)
	if len(batch) == 0 {
		return nil
	}
	request := c.incidentRequest(now, batch)
	c.mu.Lock()
	c.incidentOpen = true
	c.responderDone = false
	c.currentIncidentRequest = cloneIncidentRequest(&request)
	c.currentIncidentResult = nil
	c.dispatchError = ""
	c.incidentDetectedAt = now.UTC()
	c.incidentDispatchedAt = time.Time{}
	c.responderCompletedAt = time.Time{}
	c.detectorReviewRequired = false
	c.detectorReviewRequiredAt = time.Time{}
	c.detectorReviewReason = ""
	c.dispatchState = "pending"
	if c.broker != nil {
		c.dispatchState = "workspace_pending"
	}
	c.incidentFindingKeys = findingKeys(batch)
	c.mu.Unlock()
	return nil
}

func severityAtLeast(actual sdk.FindingSeverity, threshold sdk.FindingSeverity) bool {
	rank := map[sdk.FindingSeverity]int{
		sdk.SeverityInfo: 1, sdk.SeverityWarning: 2, sdk.SeverityCritical: 3,
	}
	return rank[actual] >= rank[threshold]
}

func (c *Controller) NextWake() time.Time {
	next := c.scheduler.NextRun()
	if deadline, ok := c.batcher.Deadline(); ok && (next.IsZero() || deadline.Before(next)) {
		return deadline
	}
	c.mu.Lock()
	verificationDeadline := c.responderCompletedAt.Add(c.config.VerificationTimeout)
	verificationPending := c.incidentOpen && c.responderDone && !c.detectorReviewRequired
	c.mu.Unlock()
	if verificationPending && (next.IsZero() || verificationDeadline.Before(next)) {
		return verificationDeadline
	}
	return next
}

func (c *Controller) IncidentOpen() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.incidentOpen
}

func (c *Controller) recordEvaluation(detectorID string, now time.Time, findings []sdk.Finding) {
	status := DetectorEvaluationClear
	fingerprints := make([]string, 0, len(findings))
	for _, finding := range findings {
		if finding.Status == sdk.FindingActive {
			status = DetectorEvaluationFiring
			fingerprints = append(fingerprints, finding.Fingerprint)
		}
	}
	sort.Strings(fingerprints)
	c.history = append(c.history, DetectorEvaluation{
		DetectorID: detectorID, EvaluatedAt: now.UTC(), Status: status, Fingerprints: fingerprints,
	})
	if len(c.history) > 100 {
		c.history = append([]DetectorEvaluation(nil), c.history[len(c.history)-100:]...)
	}
}

func (c *Controller) recordDetectorError(detectorID string, now time.Time, err error) {
	c.history = append(c.history, DetectorEvaluation{
		DetectorID: detectorID, EvaluatedAt: now.UTC(), Status: DetectorEvaluationError, Error: err.Error(),
		Fingerprints: []string{},
	})
	if c.OnError != nil {
		c.OnError(fmt.Errorf("detector %q: %w", detectorID, err))
	}
}

func (c *Controller) incidentRequest(now time.Time, findings []sdk.Finding) IncidentRequest {
	incidentID := fmt.Sprintf("%s-%d", c.config.Application, now.UnixNano())
	return IncidentRequest{
		SchemaVersion: ProtocolSchemaVersion, Application: c.config.Application, Namespace: c.config.Namespace,
		IncidentID: incidentID, Findings: findings,
		DetectorHistory: compactDetectorHistory(c.history), SurfacedPlaybooks: surfacedPlaybooks(findings),
		RelevantOutcomes: relevantOutcomeEvidence(c.config.RepositoryWorktree, findings, c.config.SourceCommit),
		SourceCommit:     c.config.SourceCommit, DeployedCommit: c.config.DeployedCommit,
		ArchitectureSummaryPath: c.config.ArchitectureSummaryPath, HealthObjectivePath: c.config.HealthObjectivePath,
		RepositoryWorktree: c.config.RepositoryWorktree, RepositoryBaseCommit: c.config.SourceCommit,
		ResponseDeadline:  now.Add(c.config.ResponseTimeout).UTC(),
		CancellationToken: "cancel-" + incidentID,
		RepairPolicy:      c.config.RepairPolicy,
	}
}

func compactDetectorHistory(history []DetectorEvaluation) []DetectorEvaluation {
	compacted := make([]DetectorEvaluation, 0, len(history))
	keys := make([]string, 0, len(history))
	for _, evaluation := range history {
		key := evaluation.DetectorID + "\x00" + string(evaluation.Status) + "\x00" +
			strings.Join(evaluation.Fingerprints, "\x00") + "\x00" + evaluation.Error
		if len(keys) >= 2 && key == keys[len(keys)-1] && key == keys[len(keys)-2] {
			compacted[len(compacted)-1] = evaluation
			continue
		}
		compacted = append(compacted, evaluation)
		keys = append(keys, key)
	}
	const limit = 12
	if len(compacted) > limit {
		compacted = compacted[len(compacted)-limit:]
	}
	return append([]DetectorEvaluation(nil), compacted...)
}

func surfacedPlaybooks(findings []sdk.Finding) []SurfacedPlaybook {
	byPath := make(map[string]SurfacedPlaybook)
	for _, finding := range findings {
		for _, path := range finding.Playbooks {
			byPath[path] = SurfacedPlaybook{Path: path, ParameterBindings: finding.ParameterBindings}
		}
	}
	result := make([]SurfacedPlaybook, 0, len(byPath))
	for _, playbook := range byPath {
		result = append(result, playbook)
	}
	sort.Slice(result, func(left int, right int) bool { return result[left].Path < result[right].Path })
	return result
}

func (c *Controller) processDispatchCompletions(observedAt time.Time) {
	for {
		select {
		case completion := <-c.results:
			c.handleDispatchCompletion(completion, observedAt)
		default:
			return
		}
	}
}

func (c *Controller) handleDispatchCompletion(completion dispatchCompletion, observedAt time.Time) {
	c.mu.Lock()
	if c.currentIncidentRequest == nil || completion.incidentID != c.currentIncidentRequest.IncidentID {
		currentIncidentID := ""
		if c.currentIncidentRequest != nil {
			currentIncidentID = c.currentIncidentRequest.IncidentID
		}
		c.mu.Unlock()
		c.reportBrokerError(
			"ignore stale dispatch completion",
			fmt.Errorf("completion incident %q does not match current incident %q", completion.incidentID, currentIncidentID),
		)
		return
	}
	if completion.err != nil {
		// A watcher, transport, or leadership-guard failure does not prove that
		// the durable responder Job stopped. Retry the same incident effect so
		// the idempotent dispatcher rejoins its existing Job/result instead of
		// clearing the incident lock and opening a duplicate responder.
		c.responderDone = false
		c.responderCompletedAt = time.Time{}
		c.dispatchState = "pending"
		c.dispatchError = completion.err.Error()
		c.currentIncidentResult = nil
	} else {
		c.responderDone = true
		c.responderCompletedAt = observedAt.UTC()
		if c.responderCompletedAt.Before(c.incidentDispatchedAt) {
			c.responderCompletedAt = c.incidentDispatchedAt
		}
		c.dispatchState = "completed"
		c.dispatchError = ""
		c.currentIncidentResult = cloneIncidentResult(&completion.result)
	}
	c.mu.Unlock()
	if completion.err != nil && c.OnError != nil {
		c.OnError(fmt.Errorf("dispatch incident: %w", completion.err))
	}
	if completion.err == nil && c.OnResult != nil {
		c.OnResult(completion.result)
	}
}

func (c *Controller) maybeCloseIncident(now time.Time) {
	c.mu.Lock()
	if !c.incidentOpen || !c.responderDone || c.currentIncidentRequest == nil {
		c.mu.Unlock()
		return
	}
	finalStates, verified := c.finalVerificationStates()
	if !verified {
		if c.detectorReviewRequired {
			c.mu.Unlock()
			return
		}
		deadline := c.responderCompletedAt.Add(c.config.VerificationTimeout)
		if !now.Before(deadline) {
			c.detectorReviewRequired = true
			c.detectorReviewRequiredAt = now.UTC()
			c.detectorReviewReason = fmt.Sprintf(
				"health detectors did not clear within %s after responder completion",
				c.config.VerificationTimeout,
			)
		}
		c.mu.Unlock()
		return
	}
	verifiedAt := now.UTC()
	if verifiedAt.Before(c.responderCompletedAt) {
		verifiedAt = c.responderCompletedAt
	}
	closure := IncidentClosure{
		Request: *cloneIncidentRequest(c.currentIncidentRequest), Result: cloneIncidentResult(c.currentIncidentResult),
		DispatchError: c.dispatchError, FinalDetectorStates: finalStates,
		DetectedAt: c.incidentDetectedAt, DispatchedAt: c.incidentDispatchedAt,
		ResponderCompletedAt: c.responderCompletedAt, VerifiedAt: verifiedAt,
	}
	c.pendingClosure = cloneIncidentClosure(&closure)
	c.closureState = "pending"
	c.closureReceipt = nil
	c.incidentOpen = false
	c.responderDone = false
	c.currentIncidentRequest = nil
	c.currentIncidentResult = nil
	c.dispatchError = ""
	c.incidentDetectedAt = time.Time{}
	c.incidentDispatchedAt = time.Time{}
	c.responderCompletedAt = time.Time{}
	c.detectorReviewRequired = false
	c.detectorReviewRequiredAt = time.Time{}
	c.detectorReviewReason = ""
	c.dispatchState = "idle"
	c.incidentFindingKeys = nil
	c.mu.Unlock()
	if c.OnIncidentClosed != nil {
		c.OnIncidentClosed(closure)
	}
}

func (c *Controller) finalVerificationStates() ([]DetectorEvaluation, bool) {
	if len(c.healthDetectorIDs) == 0 {
		if c.tracker.HasActiveKeys(c.incidentFindingKeys) {
			return nil, false
		}
		ids := make([]string, 0)
		seen := make(map[string]struct{})
		for _, finding := range c.currentIncidentRequest.Findings {
			if _, ok := seen[finding.DetectorID]; !ok {
				seen[finding.DetectorID] = struct{}{}
				ids = append(ids, finding.DetectorID)
			}
		}
		sort.Strings(ids)
		return c.latestEvaluations(ids, time.Time{}, false)
	}
	for _, detectorID := range c.healthDetectorIDs {
		if c.tracker.HasActiveDetector(detectorID) {
			return nil, false
		}
	}
	return c.latestEvaluations(c.healthDetectorIDs, c.responderCompletedAt, true)
}

func (c *Controller) latestEvaluations(
	detectorIDs []string,
	since time.Time,
	requireClear bool,
) ([]DetectorEvaluation, bool) {
	result := make([]DetectorEvaluation, 0, len(detectorIDs))
	for _, detectorID := range detectorIDs {
		found := false
		for index := len(c.history) - 1; index >= 0; index-- {
			evaluation := c.history[index]
			if evaluation.DetectorID != detectorID || evaluation.EvaluatedAt.Before(since) {
				continue
			}
			if requireClear && evaluation.Status != DetectorEvaluationClear {
				return nil, false
			}
			result = append(result, evaluation)
			found = true
			break
		}
		if !found && requireClear {
			return nil, false
		}
	}
	return result, true
}

func (c *Controller) ExportState() RuntimeState {
	c.mu.Lock()
	defer c.mu.Unlock()
	history := append([]DetectorEvaluation(nil), c.history...)
	if len(history) > 100 {
		history = history[len(history)-100:]
	}
	dispatchState := c.dispatchState
	if dispatchState == "running" {
		dispatchState = "pending"
	} else if dispatchState == "workspace_running" {
		dispatchState = "workspace_pending"
	}
	closureState := c.closureState
	if closureState == "processing" {
		closureState = "pending"
	} else if closureState == "acknowledging" {
		closureState = "committed"
	}
	return RuntimeState{
		Version: RuntimeStateVersion, SchedulerDeadlines: c.scheduler.Deadlines(),
		FindingStates: c.tracker.Snapshot(), PendingBatch: c.batcher.Snapshot(), History: history,
		IncidentOpen: c.incidentOpen, ResponderDone: c.responderDone,
		IncidentRequest: cloneIncidentRequest(c.currentIncidentRequest),
		IncidentResult:  cloneIncidentResult(c.currentIncidentResult), DispatchError: c.dispatchError,
		IncidentDetectedAt: c.incidentDetectedAt, IncidentDispatchedAt: c.incidentDispatchedAt,
		ResponderCompletedAt: c.responderCompletedAt, DispatchState: dispatchState,
		DetectorReviewRequired:     c.detectorReviewRequired,
		DetectorReviewRequiredAt:   c.detectorReviewRequiredAt,
		DetectorReviewReason:       c.detectorReviewReason,
		IncidentFindingKeys:        append([]string(nil), c.incidentFindingKeys...),
		PendingClosure:             cloneIncidentClosure(c.pendingClosure),
		ClosureState:               closureState,
		ClosureReceipt:             cloneClosureReceipt(c.closureReceipt),
		LastAcknowledgedIncidentID: c.lastAcknowledgedIncidentID,
	}
}

func (c *Controller) RestoreState(state RuntimeState) error {
	if err := state.Validate(); err != nil {
		return err
	}
	if err := c.scheduler.RestoreDeadlines(state.SchedulerDeadlines); err != nil {
		return err
	}
	c.tracker.Restore(state.FindingStates)
	c.batcher.Restore(state.PendingBatch)
	c.history = append([]DetectorEvaluation(nil), state.History...)
	c.mu.Lock()
	defer c.mu.Unlock()
	c.incidentOpen = state.IncidentOpen
	// A subprocess cannot survive a controller restart. Preserve the incident
	// lock, but treat the old process as finished so detector clears can close it.
	c.responderDone = state.ResponderDone
	c.currentIncidentRequest = cloneIncidentRequest(state.IncidentRequest)
	c.currentIncidentResult = cloneIncidentResult(state.IncidentResult)
	c.dispatchError = state.DispatchError
	c.incidentDetectedAt = state.IncidentDetectedAt
	c.incidentDispatchedAt = state.IncidentDispatchedAt
	c.responderCompletedAt = state.ResponderCompletedAt
	c.detectorReviewRequired = state.DetectorReviewRequired
	c.detectorReviewRequiredAt = state.DetectorReviewRequiredAt
	c.detectorReviewReason = state.DetectorReviewReason
	c.dispatchState = state.DispatchState
	c.incidentFindingKeys = append([]string(nil), state.IncidentFindingKeys...)
	c.pendingClosure = cloneIncidentClosure(state.PendingClosure)
	c.closureState = state.ClosureState
	if c.pendingClosure != nil && c.closureState == "" {
		c.closureState = "pending"
	}
	c.closureReceipt = cloneClosureReceipt(state.ClosureReceipt)
	c.lastAcknowledgedIncidentID = state.LastAcknowledgedIncidentID
	return nil
}

func (c *Controller) LastAcknowledgedIncidentID() string {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.lastAcknowledgedIncidentID
}

func (c *Controller) DetectorReviewRequired() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.detectorReviewRequired
}

func (c *Controller) DetectorReviewStatus() (bool, string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.detectorReviewRequired, c.detectorReviewReason
}

func (c *Controller) PendingIncidentClosure() (IncidentClosure, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.pendingClosure == nil {
		return IncidentClosure{}, false
	}
	return *cloneIncidentClosure(c.pendingClosure), true
}

func (c *Controller) SetIncidentBroker(broker IncidentBroker) error {
	if broker == nil {
		return fmt.Errorf("incident broker is required")
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	c.broker = broker
	return nil
}

func findingKeys(findings []sdk.Finding) []string {
	keys := make([]string, 0, len(findings))
	for _, finding := range findings {
		keys = append(keys, FindingStateKey(finding.DetectorID, FindingFingerprint(finding)))
	}
	sort.Strings(keys)
	return keys
}

func (c *Controller) AttachStateStore(ctx context.Context, store StateStore) error {
	if store == nil {
		return fmt.Errorf("state store is required")
	}
	state, revision, err := store.Load(ctx)
	if err != nil {
		return err
	}
	if revision != "" {
		if err := c.RestoreState(state); err != nil {
			return err
		}
	}
	c.stateStore = store
	c.stateRevision = revision
	return nil
}

func (c *Controller) PersistState(ctx context.Context) error {
	if c.stateStore == nil {
		return nil
	}
	actionCtx, cancel, err := c.actionContext(ctx)
	if err != nil {
		return err
	}
	defer cancel()
	revision, err := c.stateStore.Save(actionCtx, c.ExportState(), c.stateRevision)
	if err != nil {
		return err
	}
	c.stateRevision = revision
	return nil
}

func (c *Controller) actionContext(ctx context.Context) (context.Context, context.CancelFunc, error) {
	if c.GuardAction != nil {
		return c.GuardAction(ctx)
	}
	if c.CanAct != nil && !c.CanAct() {
		return nil, nil, ErrLeadershipInactive
	}
	actionCtx, cancel := context.WithCancel(ctx)
	return actionCtx, cancel, nil
}
