package core

import (
	"context"
	"testing"

	corev1 "k8s.io/api/core/v1"
	discoveryv1 "k8s.io/api/discovery/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes/fake"
)

func TestKubernetesSnapshotProviderListsNamespaceState(t *testing.T) {
	client := fake.NewSimpleClientset(
		&corev1.Service{
			ObjectMeta: metav1.ObjectMeta{Name: "api", Namespace: "demo"},
			Spec:       corev1.ServiceSpec{Selector: map[string]string{"app": "api"}},
		},
		&corev1.Pod{
			ObjectMeta: metav1.ObjectMeta{Name: "api-1", Namespace: "demo", Labels: map[string]string{"app": "api"}},
		},
		&corev1.Endpoints{
			ObjectMeta: metav1.ObjectMeta{Name: "api", Namespace: "demo"},
			Subsets: []corev1.EndpointSubset{
				{Addresses: []corev1.EndpointAddress{{IP: "10.0.0.10"}}},
			},
		},
		&discoveryv1.EndpointSlice{
			ObjectMeta: metav1.ObjectMeta{Name: "api-slice", Namespace: "demo"},
		},
		&corev1.Event{
			ObjectMeta:     metav1.ObjectMeta{Name: "api-event", Namespace: "demo"},
			InvolvedObject: corev1.ObjectReference{Kind: "Service", Namespace: "demo", Name: "api"},
		},
	)
	provider := KubernetesSnapshotProvider{Namespace: "demo", Client: client}

	snapshot, err := provider.Snapshot(context.Background())
	if err != nil {
		t.Fatalf("snapshot: %v", err)
	}

	if got := snapshot.Namespace(); got != "demo" {
		t.Fatalf("unexpected namespace %q", got)
	}
	if got := snapshot.ReadyEndpointCountForService("demo", "api"); got != 1 {
		t.Fatalf("expected one endpoint, got %d", got)
	}
	if got := snapshot.PodsForService("demo", "api"); len(got) != 1 || got[0].Name != "api-1" {
		t.Fatalf("expected matching pod, got %#v", got)
	}
	if got := snapshot.RecentEventsFor("demo", "Service", "api"); len(got) != 1 || got[0].Name != "api-event" {
		t.Fatalf("expected service event, got %#v", got)
	}
}
