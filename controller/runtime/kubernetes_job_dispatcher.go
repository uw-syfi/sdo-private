package runtime

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"path"
	"sort"
	"strings"
	"time"
	"unicode"

	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes"
)

const (
	jobRequestDataKey = "incident-request.json"
	jobResultDataKey  = "incident-result.json"
)

type KubernetesJobDispatcher struct {
	Client               kubernetes.Interface
	Namespace            string
	Image                string
	Command              []string
	ServiceAccount       string
	RepositoryPVC        string
	RepositoryMountPath  string
	RepositoryPVCSubPath string
	CredentialsSecret    string
	Environment          map[string]string
	// DispatchEnvironment adds values known only at dispatch time, such as
	// the synthetic-traffic prober's address. It overrides Environment but
	// never the controller-managed names.
	DispatchEnvironment func(context.Context) map[string]string
	PollInterval        time.Duration
}

func (d KubernetesJobDispatcher) Dispatch(ctx context.Context, request IncidentRequest) (IncidentResult, error) {
	if err := request.Validate(); err != nil {
		return IncidentResult{}, fmt.Errorf("validate incident request: %w", err)
	}
	if d.Client == nil || d.Namespace == "" || d.Image == "" || len(d.Command) == 0 || d.RepositoryPVC == "" || d.CredentialsSecret == "" {
		return IncidentResult{}, fmt.Errorf("Kubernetes client, namespace, image, command, repository PVC, and credentials Secret are required")
	}
	if d.RepositoryMountPath == "" {
		d.RepositoryMountPath = "/workspace"
	}
	worktree := path.Clean(request.RepositoryWorktree)
	mountRoot := strings.TrimSuffix(path.Clean(d.RepositoryMountPath), "/")
	if !path.IsAbs(worktree) || !path.IsAbs(mountRoot) ||
		(worktree != mountRoot && !strings.HasPrefix(worktree, mountRoot+"/")) {
		return IncidentResult{}, fmt.Errorf(
			"repository worktree %q must be inside shared repository mount %q",
			request.RepositoryWorktree,
			d.RepositoryMountPath,
		)
	}
	if d.PollInterval <= 0 {
		d.PollInterval = time.Second
	}
	jobName := IncidentJobName(request.IncidentID)
	requestName := jobName + "-request"
	resultName := jobName + "-result"
	payload, err := json.Marshal(request)
	if err != nil {
		return IncidentResult{}, fmt.Errorf("encode incident request: %w", err)
	}
	if err := d.ensureRequestConfigMap(ctx, requestName, string(payload)); err != nil {
		return IncidentResult{}, err
	}
	if err := d.ensureJob(ctx, request, jobName, requestName, resultName); err != nil {
		return IncidentResult{}, err
	}

	ticker := time.NewTicker(d.PollInterval)
	defer ticker.Stop()
	for {
		job, jobErr := d.Client.BatchV1().Jobs(d.Namespace).Get(ctx, jobName, metav1.GetOptions{})
		if jobErr != nil && !apierrors.IsNotFound(jobErr) {
			return IncidentResult{}, fmt.Errorf("get responder Job: %w", jobErr)
		}
		if jobErr == nil && jobFailed(job) {
			return IncidentResult{}, fmt.Errorf("responder Job %q failed", jobName)
		}
		resultConfigMap, err := d.Client.CoreV1().ConfigMaps(d.Namespace).Get(ctx, resultName, metav1.GetOptions{})
		if err == nil {
			payload, ok := resultConfigMap.Data[jobResultDataKey]
			if !ok {
				return IncidentResult{}, fmt.Errorf("result ConfigMap %q has no %s", resultName, jobResultDataKey)
			}
			var result IncidentResult
			if err := json.Unmarshal([]byte(payload), &result); err != nil {
				return IncidentResult{}, fmt.Errorf("decode incident result: %w", err)
			}
			if err := result.ValidateFor(request); err != nil {
				return IncidentResult{}, fmt.Errorf("validate incident result: %w", err)
			}
			// Publishing the result precedes process exit. Keep the controller's
			// incident lock until Kubernetes has durably observed Job completion;
			// otherwise a clear/re-fire during responder shutdown can open a
			// second incident and responder for the same rollout.
			if jobErr == nil && jobCompleted(job) {
				return result, nil
			}
		}
		if err != nil && !apierrors.IsNotFound(err) {
			return IncidentResult{}, fmt.Errorf("get incident result ConfigMap: %w", err)
		}
		select {
		case <-ctx.Done():
			return IncidentResult{}, ctx.Err()
		case <-ticker.C:
		}
	}
}

func (d KubernetesJobDispatcher) ensureRequestConfigMap(ctx context.Context, name string, payload string) error {
	configMaps := d.Client.CoreV1().ConfigMaps(d.Namespace)
	_, err := configMaps.Create(ctx, &corev1.ConfigMap{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: d.Namespace},
		Data:       map[string]string{jobRequestDataKey: payload},
	}, metav1.CreateOptions{})
	if err == nil {
		return nil
	}
	if !apierrors.IsAlreadyExists(err) {
		return fmt.Errorf("create incident request ConfigMap: %w", err)
	}
	existing, err := configMaps.Get(ctx, name, metav1.GetOptions{})
	if err != nil {
		return fmt.Errorf("get existing incident request ConfigMap: %w", err)
	}
	if existing.Data[jobRequestDataKey] != payload {
		return fmt.Errorf("existing incident request ConfigMap %q has different payload", name)
	}
	return nil
}

func (d KubernetesJobDispatcher) ensureJob(
	ctx context.Context,
	request IncidentRequest,
	jobName string,
	requestName string,
	resultName string,
) error {
	backoffLimit := int32(0)
	// Preserve dispatch provenance through long-running reflection and isolated
	// validation so the final production receipt can prove exact-once execution.
	ttlSecondsAfterFinished := int32(3600)
	runAsNonRoot := true
	runAsUser := int64(65532)
	allowPrivilegeEscalation := false
	readOnlyRootFilesystem := true
	job := &batchv1.Job{
		ObjectMeta: metav1.ObjectMeta{Name: jobName, Namespace: d.Namespace},
		Spec: batchv1.JobSpec{
			BackoffLimit: &backoffLimit, TTLSecondsAfterFinished: &ttlSecondsAfterFinished,
			Template: corev1.PodTemplateSpec{
				ObjectMeta: metav1.ObjectMeta{Labels: map[string]string{"app.kubernetes.io/name": "sdo-responder", "sdo.dev/incident": jobName}},
				Spec: corev1.PodSpec{
					RestartPolicy:      corev1.RestartPolicyNever,
					ServiceAccountName: d.ServiceAccount,
					SecurityContext: &corev1.PodSecurityContext{
						RunAsNonRoot: &runAsNonRoot, RunAsUser: &runAsUser,
						SeccompProfile: &corev1.SeccompProfile{Type: corev1.SeccompProfileTypeRuntimeDefault},
					},
					Containers: []corev1.Container{{
						Name: "responder", Image: d.Image, Command: append([]string(nil), d.Command...),
						WorkingDir: request.RepositoryWorktree,
						EnvFrom: []corev1.EnvFromSource{{
							SecretRef: &corev1.SecretEnvSource{LocalObjectReference: corev1.LocalObjectReference{Name: d.CredentialsSecret}},
						}},
						Env: append([]corev1.EnvVar{
							{Name: "SDO_REQUEST_CONFIGMAP", Value: requestName},
							{Name: "SDO_RESULT_CONFIGMAP", Value: resultName},
							{Name: "SDO_NAMESPACE", Value: d.Namespace},
						}, responderEnvironment(d.environment(ctx))...),
						VolumeMounts: []corev1.VolumeMount{
							{Name: "request", MountPath: "/sdo/request", ReadOnly: true},
							{Name: "repository", MountPath: d.RepositoryMountPath, SubPath: d.RepositoryPVCSubPath},
							{Name: "scratch", MountPath: "/tmp"},
							{Name: "credentials", MountPath: "/sdo/credentials", ReadOnly: true},
						},
						SecurityContext: &corev1.SecurityContext{
							AllowPrivilegeEscalation: &allowPrivilegeEscalation,
							ReadOnlyRootFilesystem:   &readOnlyRootFilesystem,
							Capabilities:             &corev1.Capabilities{Drop: []corev1.Capability{"ALL"}},
						},
						Resources: corev1.ResourceRequirements{
							Requests: corev1.ResourceList{
								corev1.ResourceCPU: resource.MustParse("250m"), corev1.ResourceMemory: resource.MustParse("512Mi"),
							},
							Limits: corev1.ResourceList{
								corev1.ResourceCPU: resource.MustParse("2"), corev1.ResourceMemory: resource.MustParse("4Gi"),
							},
						},
					}},
					Volumes: []corev1.Volume{
						{Name: "request", VolumeSource: corev1.VolumeSource{
							ConfigMap: &corev1.ConfigMapVolumeSource{LocalObjectReference: corev1.LocalObjectReference{Name: requestName}},
						}},
						{Name: "repository", VolumeSource: corev1.VolumeSource{
							PersistentVolumeClaim: &corev1.PersistentVolumeClaimVolumeSource{ClaimName: d.RepositoryPVC},
						}},
						{Name: "scratch", VolumeSource: corev1.VolumeSource{
							EmptyDir: &corev1.EmptyDirVolumeSource{},
						}},
						{Name: "credentials", VolumeSource: corev1.VolumeSource{
							Secret: &corev1.SecretVolumeSource{SecretName: d.CredentialsSecret},
						}},
					},
				},
			},
		},
	}
	if _, err := d.Client.BatchV1().Jobs(d.Namespace).Create(ctx, job, metav1.CreateOptions{}); err != nil && !apierrors.IsAlreadyExists(err) {
		return fmt.Errorf("create responder Job: %w", err)
	}
	return nil
}

func (d KubernetesJobDispatcher) environment(ctx context.Context) map[string]string {
	values := make(map[string]string, len(d.Environment))
	for name, value := range d.Environment {
		values[name] = value
	}
	if d.DispatchEnvironment != nil {
		for name, value := range d.DispatchEnvironment(ctx) {
			values[name] = value
		}
	}
	return values
}

func responderEnvironment(values map[string]string) []corev1.EnvVar {
	reserved := map[string]bool{
		"SDO_REQUEST_CONFIGMAP": true,
		"SDO_RESULT_CONFIGMAP":  true,
		"SDO_NAMESPACE":         true,
	}
	names := make([]string, 0, len(values))
	for name := range values {
		if !reserved[name] {
			names = append(names, name)
		}
	}
	sort.Strings(names)
	result := make([]corev1.EnvVar, 0, len(names))
	for _, name := range names {
		result = append(result, corev1.EnvVar{Name: name, Value: values[name]})
	}
	return result
}

func jobFailed(job *batchv1.Job) bool {
	if job.Status.Failed > 0 && job.Status.Active == 0 {
		return true
	}
	for _, condition := range job.Status.Conditions {
		if condition.Type == batchv1.JobFailed && condition.Status == corev1.ConditionTrue {
			return true
		}
	}
	return false
}

func jobCompleted(job *batchv1.Job) bool {
	if job.Status.Succeeded > 0 {
		return true
	}
	for _, condition := range job.Status.Conditions {
		if condition.Type == batchv1.JobComplete && condition.Status == corev1.ConditionTrue {
			return true
		}
	}
	return false
}

func IncidentJobName(incidentID string) string {
	hash := sha256.Sum256([]byte(incidentID))
	suffix := hex.EncodeToString(hash[:])[:10]
	var builder strings.Builder
	for _, character := range strings.ToLower(incidentID) {
		if unicode.IsLetter(character) || unicode.IsDigit(character) {
			builder.WriteRune(character)
		} else if builder.Len() > 0 && builder.String()[builder.Len()-1] != '-' {
			builder.WriteByte('-')
		}
		if builder.Len() >= 40 {
			break
		}
	}
	prefix := strings.Trim(builder.String(), "-")
	if prefix == "" {
		prefix = "incident"
	}
	return "sdo-" + prefix + "-" + suffix
}
