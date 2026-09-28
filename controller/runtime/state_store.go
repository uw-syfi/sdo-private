package runtime

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"sync"
	"time"

	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes"

	"sdo.dev/controller/sdk"
)

const (
	RuntimeStateVersion     = "sdo.controller/v1"
	stateDataKey            = "runtime-state.json"
	stateRevisionAnnotation = "sdo.dev/state-revision"
)

var ErrStateConflict = errors.New("controller state conflict")

type RuntimeState struct {
	Version                    string                  `json:"version"`
	SchedulerDeadlines         map[string]time.Time    `json:"scheduler_deadlines"`
	FindingStates              map[string]FindingState `json:"finding_states"`
	PendingBatch               BatcherState            `json:"pending_batch"`
	History                    []DetectorEvaluation    `json:"history"`
	IncidentOpen               bool                    `json:"incident_open"`
	ResponderDone              bool                    `json:"responder_done"`
	IncidentRequest            *IncidentRequest        `json:"incident_request,omitempty"`
	IncidentResult             *IncidentResult         `json:"incident_result,omitempty"`
	DispatchError              string                  `json:"dispatch_error,omitempty"`
	IncidentDetectedAt         time.Time               `json:"incident_detected_at,omitempty"`
	IncidentDispatchedAt       time.Time               `json:"incident_dispatched_at,omitempty"`
	ResponderCompletedAt       time.Time               `json:"responder_completed_at,omitempty"`
	DetectorReviewRequired     bool                    `json:"detector_review_required,omitempty"`
	DetectorReviewRequiredAt   time.Time               `json:"detector_review_required_at,omitempty"`
	DetectorReviewReason       string                  `json:"detector_review_reason,omitempty"`
	DispatchState              string                  `json:"dispatch_state"`
	IncidentFindingKeys        []string                `json:"incident_finding_keys"`
	PendingClosure             *IncidentClosure        `json:"pending_closure,omitempty"`
	ClosureState               string                  `json:"closure_state,omitempty"`
	ClosureReceipt             *ClosureReceipt         `json:"closure_receipt,omitempty"`
	ClosureFailure             *ClosureFailure         `json:"closure_failure,omitempty"`
	LastAcknowledgedIncidentID string                  `json:"last_acknowledged_incident_id,omitempty"`
	// IncidentView is published for responders and never restored.
	IncidentView *IncidentView `json:"incident_view,omitempty"`
}

func (state RuntimeState) Validate() error {
	if state.Version != RuntimeStateVersion {
		return fmt.Errorf("unsupported runtime state version %q", state.Version)
	}
	if len(state.History) > 100 {
		return fmt.Errorf("runtime history exceeds 100 entries")
	}
	if state.IncidentOpen && state.IncidentRequest == nil {
		return fmt.Errorf("open incident is missing its request")
	}
	if state.PendingClosure != nil && state.PendingClosure.Request.IncidentID == "" {
		return fmt.Errorf("pending incident closure is missing its incident id")
	}
	if err := validateClosureFailure(state); err != nil {
		return err
	}
	if state.DetectorReviewRequired && (!state.IncidentOpen || !state.ResponderDone ||
		state.DetectorReviewRequiredAt.IsZero() || state.DetectorReviewReason == "") {
		return fmt.Errorf("detector review state is incomplete")
	}
	return nil
}

type StateStore interface {
	Load(context.Context) (RuntimeState, string, error)
	Save(context.Context, RuntimeState, string) (string, error)
}

// errStaleResourceVersion reports an Update rejected because the ConfigMap
// changed since the store last read or wrote it. The state revision decides
// whether another writer took over or only metadata moved.
var errStaleResourceVersion = fmt.Errorf("%w: stale ConfigMap resourceVersion", ErrStateConflict)

// ConfigMapStateStore persists RuntimeState in one ConfigMap. The
// sdo.dev/state-revision annotation is the controller's compare-and-swap
// token. The store caches the ConfigMap it last read or wrote, so an unchanged
// state costs no request and a changed state costs one Update carrying the
// cached resourceVersion. Only a resourceVersion conflict falls back to Get
// plus Update, and that path still rejects a stale state revision.
type ConfigMapStateStore struct {
	client    kubernetes.Interface
	namespace string
	name      string

	mu sync.Mutex
	// cached is the ConfigMap as the API server last returned it, or nil when
	// no object is known or a write's outcome is unknown.
	cached *corev1.ConfigMap
	// durableDigest is the SHA-256 of cached's serialized state.
	durableDigest [sha256.Size]byte
}

func NewConfigMapStateStore(client kubernetes.Interface, namespace string, name string) *ConfigMapStateStore {
	return &ConfigMapStateStore{client: client, namespace: namespace, name: name}
}

func (s *ConfigMapStateStore) Load(ctx context.Context) (RuntimeState, string, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.cached = nil
	configMap, err := s.client.CoreV1().ConfigMaps(s.namespace).Get(ctx, s.name, metav1.GetOptions{})
	if apierrors.IsNotFound(err) {
		return RuntimeState{}, "", nil
	}
	if err != nil {
		return RuntimeState{}, "", fmt.Errorf("get controller state ConfigMap: %w", err)
	}
	payload, ok := configMap.Data[stateDataKey]
	if !ok {
		return RuntimeState{}, "", fmt.Errorf("controller state ConfigMap has no %s", stateDataKey)
	}
	var state RuntimeState
	if err := json.Unmarshal([]byte(payload), &state); err != nil {
		return RuntimeState{}, "", fmt.Errorf("decode controller state: %w", err)
	}
	if err := state.Validate(); err != nil {
		return RuntimeState{}, "", err
	}
	revision := configMap.Annotations[stateRevisionAnnotation]
	if _, err := strconv.ParseUint(revision, 10, 64); err != nil {
		return RuntimeState{}, "", fmt.Errorf("invalid controller state revision %q", revision)
	}
	s.remember(configMap)
	return state, revision, nil
}

// Save durably writes state if it differs from the last durable state and
// returns the new revision. A nil error means the state is durable at the
// returned revision. After an error nothing new is known to be durable, and
// the next Save re-reads the ConfigMap instead of trusting the cache.
func (s *ConfigMapStateStore) Save(ctx context.Context, state RuntimeState, expectedRevision string) (string, error) {
	if err := state.Validate(); err != nil {
		return "", err
	}
	payload, err := json.Marshal(state)
	if err != nil {
		return "", fmt.Errorf("encode controller state: %w", err)
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.cached != nil && expectedRevision != "" &&
		s.cached.Annotations[stateRevisionAnnotation] == expectedRevision {
		if sha256.Sum256(payload) == s.durableDigest {
			return expectedRevision, nil
		}
		revision, updateErr := s.update(ctx, s.cached, payload)
		if !errors.Is(updateErr, errStaleResourceVersion) {
			return revision, updateErr
		}
	}
	return s.saveFromServer(ctx, payload, expectedRevision)
}

// saveFromServer is the compare-and-swap path against a freshly read ConfigMap.
func (s *ConfigMapStateStore) saveFromServer(ctx context.Context, payload []byte, expectedRevision string) (string, error) {
	s.cached = nil
	configMaps := s.client.CoreV1().ConfigMaps(s.namespace)
	current, err := configMaps.Get(ctx, s.name, metav1.GetOptions{})
	if apierrors.IsNotFound(err) {
		if expectedRevision != "" {
			return "", ErrStateConflict
		}
		created, createErr := configMaps.Create(ctx, &corev1.ConfigMap{
			ObjectMeta: metav1.ObjectMeta{
				Name: s.name, Namespace: s.namespace,
				Annotations: map[string]string{stateRevisionAnnotation: "1"},
			},
			Data: map[string]string{stateDataKey: string(payload)},
		}, metav1.CreateOptions{})
		if apierrors.IsAlreadyExists(createErr) {
			return "", ErrStateConflict
		}
		if createErr != nil {
			return "", fmt.Errorf("create controller state ConfigMap: %w", createErr)
		}
		s.remember(created)
		return created.Annotations[stateRevisionAnnotation], nil
	}
	if err != nil {
		return "", fmt.Errorf("get controller state ConfigMap: %w", err)
	}
	s.remember(current)
	if expectedRevision == "" || current.Annotations[stateRevisionAnnotation] != expectedRevision {
		return "", ErrStateConflict
	}
	return s.update(ctx, current, payload)
}

// update replaces base's state payload, preserving its other metadata, with
// base's resourceVersion as the API server's precondition. Callers hold s.mu.
func (s *ConfigMapStateStore) update(ctx context.Context, base *corev1.ConfigMap, payload []byte) (string, error) {
	currentRevision := base.Annotations[stateRevisionAnnotation]
	revision, err := strconv.ParseUint(currentRevision, 10, 64)
	if err != nil {
		return "", fmt.Errorf("invalid controller state revision %q", currentRevision)
	}
	nextRevision := strconv.FormatUint(revision+1, 10)
	updated := base.DeepCopy()
	if updated.Annotations == nil {
		updated.Annotations = make(map[string]string)
	}
	updated.Annotations[stateRevisionAnnotation] = nextRevision
	updated.Data = map[string]string{stateDataKey: string(payload)}
	s.cached = nil
	written, err := s.client.CoreV1().ConfigMaps(s.namespace).Update(ctx, updated, metav1.UpdateOptions{})
	if err != nil {
		if apierrors.IsConflict(err) {
			return "", errStaleResourceVersion
		}
		return "", fmt.Errorf("update controller state ConfigMap: %w", err)
	}
	if written == nil {
		written = updated
	}
	s.remember(written)
	return nextRevision, nil
}

// remember caches configMap as the durable object. Callers hold s.mu.
func (s *ConfigMapStateStore) remember(configMap *corev1.ConfigMap) {
	s.cached = configMap.DeepCopy()
	s.durableDigest = sha256.Sum256([]byte(configMap.Data[stateDataKey]))
}

func cloneIncidentRequest(request *IncidentRequest) *IncidentRequest {
	if request == nil {
		return nil
	}
	copy := *request
	copy.Findings = append([]sdk.Finding(nil), request.Findings...)
	copy.DetectorHistory = append([]DetectorEvaluation(nil), request.DetectorHistory...)
	copy.SurfacedPlaybooks = append([]SurfacedPlaybook(nil), request.SurfacedPlaybooks...)
	return &copy
}

func cloneIncidentResult(result *IncidentResult) *IncidentResult {
	if result == nil {
		return nil
	}
	copy := *result
	copy.ConfirmedRootCauses = append(make([]ConfirmedRootCause, 0, len(result.ConfirmedRootCauses)), result.ConfirmedRootCauses...)
	for index := range copy.ConfirmedRootCauses {
		copy.ConfirmedRootCauses[index].Resources = append(
			make([]sdk.ObjectRef, 0, len(result.ConfirmedRootCauses[index].Resources)),
			result.ConfirmedRootCauses[index].Resources...,
		)
	}
	copy.AppliedPlaybooks = append(make([]AppliedPlaybook, 0, len(result.AppliedPlaybooks)), result.AppliedPlaybooks...)
	for index := range copy.AppliedPlaybooks {
		copy.AppliedPlaybooks[index].Scripts = append(
			make([]string, 0, len(result.AppliedPlaybooks[index].Scripts)), result.AppliedPlaybooks[index].Scripts...,
		)
		copy.AppliedPlaybooks[index].ParameterBindings = make(map[string]sdk.ObjectRef)
		for name, resource := range result.AppliedPlaybooks[index].ParameterBindings {
			copy.AppliedPlaybooks[index].ParameterBindings[name] = resource
		}
	}
	copy.RepairChanges = append(make([]string, 0, len(result.RepairChanges)), result.RepairChanges...)
	copy.RepairActions = append(
		make([]RepairActionReceipt, 0, len(result.RepairActions)), result.RepairActions...,
	)
	copy.FinalDetectorStates = append(
		make([]DetectorEvaluation, 0, len(result.FinalDetectorStates)), result.FinalDetectorStates...,
	)
	copy.ProposedMemoryChanges = append(
		make([]string, 0, len(result.ProposedMemoryChanges)), result.ProposedMemoryChanges...,
	)
	copy.VerificationEvidence = append(
		make([]VerificationEvidence, 0, len(result.VerificationEvidence)), result.VerificationEvidence...,
	)
	return &copy
}

func cloneIncidentClosure(closure *IncidentClosure) *IncidentClosure {
	if closure == nil {
		return nil
	}
	copy := *closure
	copy.Request = *cloneIncidentRequest(&closure.Request)
	copy.Result = cloneIncidentResult(closure.Result)
	copy.FinalDetectorStates = append([]DetectorEvaluation(nil), closure.FinalDetectorStates...)
	copy.IncidentDetectorStates = append([]DetectorEvaluation(nil), closure.IncidentDetectorStates...)
	return &copy
}

func cloneClosureReceipt(receipt *ClosureReceipt) *ClosureReceipt {
	if receipt == nil {
		return nil
	}
	copy := *receipt
	return &copy
}
