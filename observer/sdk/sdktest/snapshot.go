package sdktest

import (
	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	discoveryv1 "k8s.io/api/discovery/v1"
	"k8s.io/apimachinery/pkg/labels"
)

type Snapshot struct {
	NamespaceName     string
	ServiceList       []corev1.Service
	PodList           []corev1.Pod
	DeploymentList    []appsv1.Deployment
	ReplicaSetList    []appsv1.ReplicaSet
	EndpointList      []corev1.Endpoints
	EndpointSliceList []discoveryv1.EndpointSlice
	EventList         []corev1.Event
}

func (s Snapshot) Namespace() string {
	return s.NamespaceName
}

func (s Snapshot) Services() []corev1.Service {
	return s.ServiceList
}

func (s Snapshot) Pods() []corev1.Pod {
	return s.PodList
}

func (s Snapshot) Deployments() []appsv1.Deployment {
	return s.DeploymentList
}

func (s Snapshot) ReplicaSets() []appsv1.ReplicaSet {
	return s.ReplicaSetList
}

func (s Snapshot) Endpoints() []corev1.Endpoints {
	return s.EndpointList
}

func (s Snapshot) EndpointSlices() []discoveryv1.EndpointSlice {
	return s.EndpointSliceList
}

func (s Snapshot) Events() []corev1.Event {
	return s.EventList
}

func (s Snapshot) ReadyEndpointCountForService(namespace string, service string) int {
	ready := 0
	for _, endpoints := range s.EndpointList {
		if endpoints.Namespace != namespace || endpoints.Name != service {
			continue
		}
		for _, subset := range endpoints.Subsets {
			ready += len(subset.Addresses)
		}
	}
	return ready
}

func (s Snapshot) PodsForService(namespace string, service string) []corev1.Pod {
	var selector labels.Selector
	for _, candidate := range s.ServiceList {
		if candidate.Namespace == namespace && candidate.Name == service {
			selector = labels.SelectorFromSet(candidate.Spec.Selector)
			break
		}
	}
	if selector == nil || selector.Empty() {
		return nil
	}

	pods := make([]corev1.Pod, 0)
	for _, pod := range s.PodList {
		if pod.Namespace == namespace && selector.Matches(labels.Set(pod.Labels)) {
			pods = append(pods, pod)
		}
	}
	return pods
}

func (s Snapshot) RecentEventsFor(namespace string, kind string, name string) []corev1.Event {
	events := make([]corev1.Event, 0)
	for _, event := range s.EventList {
		ref := event.InvolvedObject
		if ref.Namespace == namespace && ref.Kind == kind && ref.Name == name {
			events = append(events, event)
		}
	}
	return events
}
