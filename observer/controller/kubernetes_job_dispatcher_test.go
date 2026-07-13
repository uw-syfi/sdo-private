package controller

import (
	"context"
	"encoding/json"
	"errors"
	"testing"
	"time"

	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes/fake"
)

func TestKubernetesJobDispatcherCrashReplayReattachesExactlyOnce(t *testing.T) {
	client := fake.NewSimpleClientset()
	request := goldenRequest(t)
	dispatcher := KubernetesJobDispatcher{
		Client: client, Namespace: "demo", Image: "sdo-responder:test", Command: []string{"/responder"},
		ServiceAccount: "sdo-responder", RepositoryPVC: "application-repository", CredentialsSecret: "sdo-codex",
		RepositoryMountPath: "/workspace", RepositoryPVCSubPath: "shared", PollInterval: time.Millisecond,
		Environment: map[string]string{"SDO_SREGYM_API_BASE": "http://host.docker.internal:8123"},
	}
	firstCtx, cancelFirst := context.WithCancel(context.Background())
	firstResult := make(chan error, 1)
	go func() {
		_, err := dispatcher.Dispatch(firstCtx, request)
		firstResult <- err
	}()
	jobName := IncidentJobName(request.IncidentID)
	job := awaitJob(t, client, jobName)
	if job.Spec.TTLSecondsAfterFinished == nil || *job.Spec.TTLSecondsAfterFinished < 1800 {
		t.Fatalf("responder Job expires before the closure receipt can inspect it: %#v", job.Spec.TTLSecondsAfterFinished)
	}
	container := job.Spec.Template.Spec.Containers[0]
	if got := container.EnvFrom[0].SecretRef.Name; got != "sdo-codex" {
		t.Fatalf("responder did not receive isolated credentials Secret: %q", got)
	}
	if got := container.WorkingDir; got != request.RepositoryWorktree {
		t.Fatalf("responder working directory %q does not match broker worktree", got)
	}
	if !containsEnvironment(container.Env, "SDO_SREGYM_API_BASE", "http://host.docker.internal:8123") {
		t.Fatalf("responder bridge environment was not propagated: %#v", container.Env)
	}
	if got := container.VolumeMounts[1]; got.MountPath != "/workspace" || got.SubPath != "shared" {
		t.Fatalf("repository PVC was not mounted at the stable shared root: %#v", got)
	}
	if container.SecurityContext == nil || container.SecurityContext.AllowPrivilegeEscalation == nil ||
		*container.SecurityContext.AllowPrivilegeEscalation || container.SecurityContext.ReadOnlyRootFilesystem == nil ||
		!*container.SecurityContext.ReadOnlyRootFilesystem {
		t.Fatalf("responder container is not hardened: %#v", container.SecurityContext)
	}
	if job.Spec.Template.Spec.SecurityContext == nil || job.Spec.Template.Spec.SecurityContext.RunAsNonRoot == nil ||
		!*job.Spec.Template.Spec.SecurityContext.RunAsNonRoot {
		t.Fatalf("responder pod is not non-root: %#v", job.Spec.Template.Spec.SecurityContext)
	}
	cancelFirst()
	if err := <-firstResult; !errors.Is(err, context.Canceled) {
		t.Fatalf("expected first controller crash cancellation, got %v", err)
	}

	secondResult := make(chan IncidentResult, 1)
	secondError := make(chan error, 1)
	go func() {
		result, err := dispatcher.Dispatch(context.Background(), request)
		secondResult <- result
		secondError <- err
	}()
	result := completedResult(request.IncidentID)
	payload, err := json.Marshal(result)
	if err != nil {
		t.Fatalf("encode result: %v", err)
	}
	if _, err := client.CoreV1().ConfigMaps("demo").Create(context.Background(), &corev1.ConfigMap{
		ObjectMeta: metav1.ObjectMeta{Name: jobName + "-result"},
		Data:       map[string]string{jobResultDataKey: string(payload)},
	}, metav1.CreateOptions{}); err != nil {
		t.Fatalf("create result ConfigMap: %v", err)
	}
	job.Status.Succeeded = 1
	job.Status.Conditions = []batchv1.JobCondition{{Type: batchv1.JobComplete, Status: corev1.ConditionTrue}}
	if _, err := client.BatchV1().Jobs("demo").UpdateStatus(context.Background(), job, metav1.UpdateOptions{}); err != nil {
		t.Fatalf("mark replayed responder Job complete: %v", err)
	}
	if err := <-secondError; err != nil {
		t.Fatalf("reattach dispatch: %v", err)
	}
	if got := (<-secondResult).IncidentID; got != request.IncidentID {
		t.Fatalf("unexpected result incident %q", got)
	}
	jobs, err := client.BatchV1().Jobs("demo").List(context.Background(), metav1.ListOptions{})
	if err != nil {
		t.Fatalf("list Jobs: %v", err)
	}
	if len(jobs.Items) != 1 || jobs.Items[0].Name != jobName {
		t.Fatalf("dispatch replay created duplicate Jobs: %#v", jobs.Items)
	}
}

func TestKubernetesJobDispatcherDoesNotCompleteUntilResponderJobCompletes(t *testing.T) {
	client := fake.NewSimpleClientset()
	request := goldenRequest(t)
	dispatcher := KubernetesJobDispatcher{
		Client: client, Namespace: "demo", Image: "sdo-responder:test", Command: []string{"/responder"},
		ServiceAccount: "sdo-responder", RepositoryPVC: "application-repository", CredentialsSecret: "sdo-codex",
		RepositoryMountPath: "/workspace", PollInterval: time.Millisecond,
	}
	resultChannel := make(chan IncidentResult, 1)
	errorChannel := make(chan error, 1)
	go func() {
		result, err := dispatcher.Dispatch(context.Background(), request)
		resultChannel <- result
		errorChannel <- err
	}()
	jobName := IncidentJobName(request.IncidentID)
	job := awaitJob(t, client, jobName)
	result := completedResult(request.IncidentID)
	payload, err := json.Marshal(result)
	if err != nil {
		t.Fatalf("encode result: %v", err)
	}
	if _, err := client.CoreV1().ConfigMaps("demo").Create(context.Background(), &corev1.ConfigMap{
		ObjectMeta: metav1.ObjectMeta{Name: jobName + "-result"},
		Data:       map[string]string{jobResultDataKey: string(payload)},
	}, metav1.CreateOptions{}); err != nil {
		t.Fatalf("create result ConfigMap: %v", err)
	}
	select {
	case err := <-errorChannel:
		t.Fatalf("dispatcher released the incident lock while responder Job was active: %v", err)
	case <-time.After(20 * time.Millisecond):
	}
	job.Status.Succeeded = 1
	job.Status.Conditions = []batchv1.JobCondition{{Type: batchv1.JobComplete, Status: corev1.ConditionTrue}}
	if _, err := client.BatchV1().Jobs("demo").UpdateStatus(context.Background(), job, metav1.UpdateOptions{}); err != nil {
		t.Fatalf("mark responder Job complete: %v", err)
	}
	select {
	case err := <-errorChannel:
		if err != nil {
			t.Fatalf("completed responder Job returned error: %v", err)
		}
		if got := (<-resultChannel).IncidentID; got != request.IncidentID {
			t.Fatalf("unexpected result incident %q", got)
		}
	case <-time.After(time.Second):
		t.Fatal("dispatcher did not release completed responder Job result")
	}
}

func containsEnvironment(environment []corev1.EnvVar, name string, value string) bool {
	for _, variable := range environment {
		if variable.Name == name && variable.Value == value {
			return true
		}
	}
	return false
}

func TestKubernetesJobDispatcherReturnsFailedJob(t *testing.T) {
	client := fake.NewSimpleClientset()
	request := goldenRequest(t)
	dispatcher := KubernetesJobDispatcher{
		Client: client, Namespace: "demo", Image: "sdo-responder:test", Command: []string{"/responder"},
		ServiceAccount: "sdo-responder", RepositoryPVC: "application-repository", CredentialsSecret: "sdo-codex",
		RepositoryMountPath: "/workspace", PollInterval: time.Millisecond,
	}
	result := make(chan error, 1)
	go func() {
		_, err := dispatcher.Dispatch(context.Background(), request)
		result <- err
	}()
	jobName := IncidentJobName(request.IncidentID)
	job := awaitJob(t, client, jobName)
	job.Status.Failed = 1
	if _, err := client.BatchV1().Jobs("demo").UpdateStatus(context.Background(), job, metav1.UpdateOptions{}); err != nil {
		t.Fatalf("mark Job failed: %v", err)
	}
	select {
	case err := <-result:
		if err == nil {
			t.Fatal("expected failed Job error")
		}
	case <-time.After(time.Second):
		t.Fatal("timed out waiting for failed Job result")
	}
}

func TestKubernetesJobDispatcherRejectsWorktreeOutsideSharedMount(t *testing.T) {
	request := goldenRequest(t)
	request.RepositoryWorktree = "/private/host/worktree"
	dispatcher := KubernetesJobDispatcher{
		Client: fake.NewSimpleClientset(), Namespace: "demo", Image: "sdo-responder:test", Command: []string{"/responder"},
		ServiceAccount: "sdo-responder", RepositoryPVC: "application-repository", CredentialsSecret: "sdo-codex",
		RepositoryMountPath: "/workspace", PollInterval: time.Millisecond,
	}

	if _, err := dispatcher.Dispatch(context.Background(), request); err == nil {
		t.Fatal("dispatcher accepted a broker worktree outside the shared PVC mount")
	}
}

func awaitJob(t *testing.T, client *fake.Clientset, name string) *batchv1.Job {
	t.Helper()
	deadline := time.Now().Add(time.Second)
	for time.Now().Before(deadline) {
		if job, err := client.BatchV1().Jobs("demo").Get(context.Background(), name, metav1.GetOptions{}); err == nil {
			return job
		}
		time.Sleep(time.Millisecond)
	}
	t.Fatalf("timed out waiting for Job %q", name)
	return nil
}
