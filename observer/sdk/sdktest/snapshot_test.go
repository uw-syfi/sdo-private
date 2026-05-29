package sdktest

import (
	"testing"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

func TestSnapshotHelpers(t *testing.T) {
	snapshot := Snapshot{
		NamespaceName: "demo",
		ServiceList: []corev1.Service{
			{
				ObjectMeta: metav1.ObjectMeta{Name: "api", Namespace: "demo"},
				Spec:       corev1.ServiceSpec{Selector: map[string]string{"app": "api"}},
			},
		},
		PodList: []corev1.Pod{
			{ObjectMeta: metav1.ObjectMeta{Name: "api-1", Namespace: "demo", Labels: map[string]string{"app": "api"}}},
			{ObjectMeta: metav1.ObjectMeta{Name: "worker-1", Namespace: "demo", Labels: map[string]string{"app": "worker"}}},
		},
		EndpointList: []corev1.Endpoints{
			{
				ObjectMeta: metav1.ObjectMeta{Name: "api", Namespace: "demo"},
				Subsets: []corev1.EndpointSubset{
					{Addresses: []corev1.EndpointAddress{{IP: "10.0.0.1"}, {IP: "10.0.0.2"}}},
				},
			},
		},
		EventList: []corev1.Event{
			{
				ObjectMeta:     metav1.ObjectMeta{Name: "event-1", Namespace: "demo"},
				InvolvedObject: corev1.ObjectReference{Kind: "Service", Namespace: "demo", Name: "api"},
			},
		},
	}

	if got := snapshot.ReadyEndpointCountForService("demo", "api"); got != 2 {
		t.Fatalf("expected 2 ready endpoints, got %d", got)
	}
	if got := snapshot.PodsForService("demo", "api"); len(got) != 1 || got[0].Name != "api-1" {
		t.Fatalf("expected api pod, got %#v", got)
	}
	if got := snapshot.RecentEventsFor("demo", "Service", "api"); len(got) != 1 || got[0].Name != "event-1" {
		t.Fatalf("expected service event, got %#v", got)
	}
}
