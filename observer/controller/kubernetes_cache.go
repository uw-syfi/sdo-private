package controller

import (
	"context"
	"fmt"
	"sync"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	discoveryv1 "k8s.io/api/discovery/v1"
	networkingv1 "k8s.io/api/networking/v1"
	"k8s.io/apimachinery/pkg/api/meta"
	"k8s.io/client-go/informers"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/tools/cache"

	observercore "sds.dev/observer/core"
	"sds.dev/observer/sdk"
)

const defaultEventBuffer = 256

type KubernetesCacheConfig struct {
	Namespace          string
	Client             kubernetes.Interface
	StateConfigMapName string
	EventBuffer        int
}

type KubernetesCache struct {
	config    KubernetesCacheConfig
	factory   informers.SharedInformerFactory
	informers map[string]cache.SharedIndexInformer
	events    chan sdk.WatchKind
	startOnce sync.Once
}

func NewKubernetesCache(config KubernetesCacheConfig, detectors []sdk.Detector) (*KubernetesCache, error) {
	if config.Namespace == "" {
		return nil, fmt.Errorf("namespace is required")
	}
	if config.Client == nil {
		return nil, fmt.Errorf("Kubernetes client is required")
	}
	if config.EventBuffer <= 0 {
		config.EventBuffer = defaultEventBuffer
	}
	factory := informers.NewSharedInformerFactoryWithOptions(config.Client, 0, informers.WithNamespace(config.Namespace))
	result := &KubernetesCache{
		config: config, factory: factory, informers: make(map[string]cache.SharedIndexInformer),
		events: make(chan sdk.WatchKind, config.EventBuffer),
	}
	declared := make(map[string]sdk.WatchKind)
	for _, detector := range detectors {
		for _, watch := range detector.Spec().Watches {
			if watch.Namespace != "" && watch.Namespace != config.Namespace {
				return nil, fmt.Errorf("watch namespace %q does not match cache namespace %q", watch.Namespace, config.Namespace)
			}
			watch.Namespace = config.Namespace
			declared[watch.APIVersion+"/"+watch.Kind] = watch
		}
	}
	for key, watch := range declared {
		informer, err := informerFor(factory, key)
		if err != nil {
			return nil, err
		}
		result.informers[key] = informer
		if _, err := informer.AddEventHandler(result.eventHandler(watch)); err != nil {
			return nil, fmt.Errorf("register %s informer handler: %w", key, err)
		}
	}
	return result, nil
}

func (c *KubernetesCache) Start(ctx context.Context) {
	c.startOnce.Do(func() { c.factory.Start(ctx.Done()) })
}

func (c *KubernetesCache) WaitForSync(ctx context.Context) error {
	if len(c.informers) == 0 {
		return nil
	}
	syncs := make([]cache.InformerSynced, 0, len(c.informers))
	for _, informer := range c.informers {
		syncs = append(syncs, informer.HasSynced)
	}
	if !cache.WaitForCacheSync(ctx.Done(), syncs...) {
		if err := ctx.Err(); err != nil {
			return err
		}
		return fmt.Errorf("Kubernetes informer cache failed to sync")
	}
	return nil
}

func (c *KubernetesCache) Events() <-chan sdk.WatchKind {
	return c.events
}

func (c *KubernetesCache) Snapshot(context.Context) (sdk.DetectionContext, error) {
	snapshot := observercore.DetectionSnapshot{NamespaceName: c.config.Namespace}
	for key, informer := range c.informers {
		for _, object := range informer.GetStore().List() {
			switch key {
			case "v1/ConfigMap":
				item := object.(*corev1.ConfigMap)
				if item.Name != c.config.StateConfigMapName {
					snapshot.ConfigMapList = append(snapshot.ConfigMapList, *item.DeepCopy())
				}
			case "v1/Service":
				snapshot.ServiceList = append(snapshot.ServiceList, *object.(*corev1.Service).DeepCopy())
			case "v1/Pod":
				snapshot.PodList = append(snapshot.PodList, *object.(*corev1.Pod).DeepCopy())
			case "apps/v1/Deployment":
				snapshot.DeploymentList = append(snapshot.DeploymentList, *object.(*appsv1.Deployment).DeepCopy())
			case "apps/v1/ReplicaSet":
				snapshot.ReplicaSetList = append(snapshot.ReplicaSetList, *object.(*appsv1.ReplicaSet).DeepCopy())
			case "v1/Endpoints":
				snapshot.EndpointList = append(snapshot.EndpointList, *object.(*corev1.Endpoints).DeepCopy())
			case "discovery.k8s.io/v1/EndpointSlice":
				snapshot.EndpointSliceList = append(snapshot.EndpointSliceList, *object.(*discoveryv1.EndpointSlice).DeepCopy())
			case "networking.k8s.io/v1/NetworkPolicy":
				snapshot.NetworkPolicyList = append(snapshot.NetworkPolicyList, *object.(*networkingv1.NetworkPolicy).DeepCopy())
			case "v1/Event":
				snapshot.EventList = append(snapshot.EventList, *object.(*corev1.Event).DeepCopy())
			}
		}
	}
	return snapshot, nil
}

func (c *KubernetesCache) eventHandler(watch sdk.WatchKind) cache.ResourceEventHandler {
	emit := func(object any) {
		if watch.Kind == "ConfigMap" && objectName(object) == c.config.StateConfigMapName {
			return
		}
		select {
		case c.events <- watch:
		default:
		}
	}
	return cache.ResourceEventHandlerFuncs{
		AddFunc: emit,
		UpdateFunc: func(_ any, current any) {
			emit(current)
		},
		DeleteFunc: emit,
	}
}

func objectName(object any) string {
	if tombstone, ok := object.(cache.DeletedFinalStateUnknown); ok {
		object = tombstone.Obj
	}
	accessor, err := meta.Accessor(object)
	if err != nil {
		return ""
	}
	return accessor.GetName()
}

func informerFor(factory informers.SharedInformerFactory, key string) (cache.SharedIndexInformer, error) {
	switch key {
	case "v1/ConfigMap":
		return factory.Core().V1().ConfigMaps().Informer(), nil
	case "v1/Service":
		return factory.Core().V1().Services().Informer(), nil
	case "v1/Pod":
		return factory.Core().V1().Pods().Informer(), nil
	case "apps/v1/Deployment":
		return factory.Apps().V1().Deployments().Informer(), nil
	case "apps/v1/ReplicaSet":
		return factory.Apps().V1().ReplicaSets().Informer(), nil
	case "v1/Endpoints":
		return factory.Core().V1().Endpoints().Informer(), nil
	case "discovery.k8s.io/v1/EndpointSlice":
		return factory.Discovery().V1().EndpointSlices().Informer(), nil
	case "networking.k8s.io/v1/NetworkPolicy":
		return factory.Networking().V1().NetworkPolicies().Informer(), nil
	case "v1/Event":
		return factory.Core().V1().Events().Informer(), nil
	default:
		return nil, fmt.Errorf("unsupported informer resource %q", key)
	}
}
