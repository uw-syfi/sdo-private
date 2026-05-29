package core

import (
	"context"
	"fmt"
	"os"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	discoveryv1 "k8s.io/api/discovery/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/labels"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
	"k8s.io/client-go/tools/clientcmd"
	"k8s.io/client-go/util/homedir"

	"sds.dev/observer/sdk"
)

type KubernetesSnapshotProvider struct {
	Namespace string
	Client    kubernetes.Interface
}

func NewKubernetesSnapshotProvider(namespace string) (*KubernetesSnapshotProvider, error) {
	if namespace == "" {
		return nil, fmt.Errorf("namespace is required")
	}
	config, err := kubernetesConfig()
	if err != nil {
		return nil, err
	}
	client, err := kubernetes.NewForConfig(config)
	if err != nil {
		return nil, fmt.Errorf("create kubernetes client: %w", err)
	}
	return &KubernetesSnapshotProvider{Namespace: namespace, Client: client}, nil
}

func (p KubernetesSnapshotProvider) Snapshot(ctx context.Context) (sdk.DetectionContext, error) {
	if p.Client == nil {
		return DetectionSnapshot{}, fmt.Errorf("kubernetes client is required")
	}
	namespace := p.Namespace
	if namespace == "" {
		return DetectionSnapshot{}, fmt.Errorf("namespace is required")
	}

	services, err := p.Client.CoreV1().Services(namespace).List(ctx, metav1.ListOptions{})
	if err != nil {
		return DetectionSnapshot{}, fmt.Errorf("list services: %w", err)
	}
	pods, err := p.Client.CoreV1().Pods(namespace).List(ctx, metav1.ListOptions{})
	if err != nil {
		return DetectionSnapshot{}, fmt.Errorf("list pods: %w", err)
	}
	deployments, err := p.Client.AppsV1().Deployments(namespace).List(ctx, metav1.ListOptions{})
	if err != nil {
		return DetectionSnapshot{}, fmt.Errorf("list deployments: %w", err)
	}
	replicaSets, err := p.Client.AppsV1().ReplicaSets(namespace).List(ctx, metav1.ListOptions{})
	if err != nil {
		return DetectionSnapshot{}, fmt.Errorf("list replicasets: %w", err)
	}
	endpoints, err := p.Client.CoreV1().Endpoints(namespace).List(ctx, metav1.ListOptions{})
	if err != nil {
		return DetectionSnapshot{}, fmt.Errorf("list endpoints: %w", err)
	}
	endpointSlices, err := p.Client.DiscoveryV1().EndpointSlices(namespace).List(ctx, metav1.ListOptions{})
	if err != nil {
		return DetectionSnapshot{}, fmt.Errorf("list endpointslices: %w", err)
	}
	events, err := p.Client.CoreV1().Events(namespace).List(ctx, metav1.ListOptions{})
	if err != nil {
		return DetectionSnapshot{}, fmt.Errorf("list events: %w", err)
	}

	return DetectionSnapshot{
		NamespaceName:     namespace,
		ServiceList:       services.Items,
		PodList:           pods.Items,
		DeploymentList:    deployments.Items,
		ReplicaSetList:    replicaSets.Items,
		EndpointList:      endpoints.Items,
		EndpointSliceList: endpointSlices.Items,
		EventList:         events.Items,
	}, nil
}

type DetectionSnapshot struct {
	NamespaceName     string
	ServiceList       []corev1.Service
	PodList           []corev1.Pod
	DeploymentList    []appsv1.Deployment
	ReplicaSetList    []appsv1.ReplicaSet
	EndpointList      []corev1.Endpoints
	EndpointSliceList []discoveryv1.EndpointSlice
	EventList         []corev1.Event
}

func (s DetectionSnapshot) Namespace() string {
	return s.NamespaceName
}

func (s DetectionSnapshot) Services() []corev1.Service {
	return s.ServiceList
}

func (s DetectionSnapshot) Pods() []corev1.Pod {
	return s.PodList
}

func (s DetectionSnapshot) Deployments() []appsv1.Deployment {
	return s.DeploymentList
}

func (s DetectionSnapshot) ReplicaSets() []appsv1.ReplicaSet {
	return s.ReplicaSetList
}

func (s DetectionSnapshot) Endpoints() []corev1.Endpoints {
	return s.EndpointList
}

func (s DetectionSnapshot) EndpointSlices() []discoveryv1.EndpointSlice {
	return s.EndpointSliceList
}

func (s DetectionSnapshot) Events() []corev1.Event {
	return s.EventList
}

func (s DetectionSnapshot) ReadyEndpointCountForService(namespace string, service string) int {
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

func (s DetectionSnapshot) PodsForService(namespace string, service string) []corev1.Pod {
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

func (s DetectionSnapshot) RecentEventsFor(namespace string, kind string, name string) []corev1.Event {
	events := make([]corev1.Event, 0)
	for _, event := range s.EventList {
		ref := event.InvolvedObject
		if ref.Namespace == namespace && ref.Kind == kind && ref.Name == name {
			events = append(events, event)
		}
	}
	return events
}

func kubernetesConfig() (*rest.Config, error) {
	config, err := rest.InClusterConfig()
	if err == nil {
		return config, nil
	}

	loadingRules := clientcmd.NewDefaultClientConfigLoadingRules()
	if kubeconfig := os.Getenv("KUBECONFIG"); kubeconfig != "" {
		loadingRules.ExplicitPath = kubeconfig
	} else if home := homedir.HomeDir(); home != "" {
		loadingRules.ExplicitPath = home + "/.kube/config"
	}
	config, err = clientcmd.NewNonInteractiveDeferredLoadingClientConfig(
		loadingRules,
		&clientcmd.ConfigOverrides{},
	).ClientConfig()
	if err != nil {
		return nil, fmt.Errorf("load kubernetes config: %w", err)
	}
	return config, nil
}
