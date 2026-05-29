package sdk

import (
	"context"
	"time"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	discoveryv1 "k8s.io/api/discovery/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

type Detector interface {
	Spec() DetectorSpec
	Detect(context.Context, DetectionContext) ([]Finding, error)
}

type DetectorSpec struct {
	ID          string
	Description string
	Watches     []WatchKind
	Interval    time.Duration
	Playbooks   []string
}

type WatchKind struct {
	APIVersion string
	Kind       string
	Namespace  string
}

type FindingStatus string

const (
	FindingActive   FindingStatus = "active"
	FindingResolved FindingStatus = "resolved"
)

type FindingSeverity string

const (
	SeverityInfo     FindingSeverity = "info"
	SeverityWarning  FindingSeverity = "warn"
	SeverityCritical FindingSeverity = "critical"
)

type ObjectRef struct {
	APIVersion string `json:"api_version,omitempty"`
	Kind       string `json:"kind"`
	Namespace  string `json:"namespace,omitempty"`
	Name       string `json:"name"`
}

func ObjectRefFrom(kind string, apiVersion string, object metav1.Object) ObjectRef {
	return ObjectRef{
		APIVersion: apiVersion,
		Kind:       kind,
		Namespace:  object.GetNamespace(),
		Name:       object.GetName(),
	}
}

type Finding struct {
	RuleID           string          `json:"rule_id"`
	Status           FindingStatus   `json:"status"`
	Severity         FindingSeverity `json:"severity"`
	Summary          string          `json:"summary"`
	Evidence         string          `json:"evidence"`
	PrimaryResource  ObjectRef       `json:"primary_resource"`
	RelatedResources []ObjectRef     `json:"related_resources,omitempty"`
	Playbooks        []string        `json:"playbooks,omitempty"`
	Fingerprint      string          `json:"fingerprint,omitempty"`
	Metadata         map[string]any  `json:"metadata,omitempty"`
}

type DetectionContext interface {
	Namespace() string
	Services() []corev1.Service
	Pods() []corev1.Pod
	Deployments() []appsv1.Deployment
	ReplicaSets() []appsv1.ReplicaSet
	Endpoints() []corev1.Endpoints
	EndpointSlices() []discoveryv1.EndpointSlice
	Events() []corev1.Event
	ReadyEndpointCountForService(namespace string, service string) int
	PodsForService(namespace string, service string) []corev1.Pod
	RecentEventsFor(namespace string, kind string, name string) []corev1.Event
}
