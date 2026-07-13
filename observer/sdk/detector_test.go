package sdk

import (
	"reflect"
	"testing"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

func TestConfigMapReferencesForDeploymentFindsAllPodTemplateReferenceForms(t *testing.T) {
	optional := true
	required := false
	deployment := appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{Name: "api", Namespace: "demo"},
		Spec: appsv1.DeploymentSpec{
			Template: corev1.PodTemplateSpec{
				Spec: corev1.PodSpec{
					Volumes: []corev1.Volume{
						{
							Name: "volume-config",
							VolumeSource: corev1.VolumeSource{
								ConfigMap: &corev1.ConfigMapVolumeSource{
									LocalObjectReference: corev1.LocalObjectReference{Name: "volume-config"},
								},
							},
						},
						{
							Name: "projected-config",
							VolumeSource: corev1.VolumeSource{
								Projected: &corev1.ProjectedVolumeSource{
									Sources: []corev1.VolumeProjection{
										{
											ConfigMap: &corev1.ConfigMapProjection{
												LocalObjectReference: corev1.LocalObjectReference{Name: "projected-config"},
												Optional:             &optional,
											},
										},
									},
								},
							},
						},
					},
					InitContainers: []corev1.Container{
						{
							Name: "init",
							Env: []corev1.EnvVar{
								{
									Name: "SETTING",
									ValueFrom: &corev1.EnvVarSource{
										ConfigMapKeyRef: &corev1.ConfigMapKeySelector{
											LocalObjectReference: corev1.LocalObjectReference{Name: "init-config"},
											Optional:             &required,
										},
									},
								},
							},
						},
					},
					Containers: []corev1.Container{
						{
							Name: "app",
							EnvFrom: []corev1.EnvFromSource{
								{
									ConfigMapRef: &corev1.ConfigMapEnvSource{
										LocalObjectReference: corev1.LocalObjectReference{Name: "env-config"},
										Optional:             &optional,
									},
								},
							},
						},
					},
				},
			},
		},
	}

	got := ConfigMapReferencesForDeployment(deployment)
	want := []ConfigMapReference{
		{Name: "env-config", Optional: true},
		{Name: "init-config", Optional: false},
		{Name: "projected-config", Optional: true},
		{Name: "volume-config", Optional: false},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("unexpected ConfigMap references:\n got: %#v\nwant: %#v", got, want)
	}
}

func TestConfigMapReferencesForDeploymentDeduplicatesAndRequiredReferenceWins(t *testing.T) {
	optional := true
	deployment := appsv1.Deployment{
		Spec: appsv1.DeploymentSpec{
			Template: corev1.PodTemplateSpec{
				Spec: corev1.PodSpec{
					Volumes: []corev1.Volume{
						{
							VolumeSource: corev1.VolumeSource{
								ConfigMap: &corev1.ConfigMapVolumeSource{
									LocalObjectReference: corev1.LocalObjectReference{Name: "shared"},
									Optional:             &optional,
								},
							},
						},
					},
					Containers: []corev1.Container{
						{
							EnvFrom: []corev1.EnvFromSource{
								{
									ConfigMapRef: &corev1.ConfigMapEnvSource{
										LocalObjectReference: corev1.LocalObjectReference{Name: "shared"},
									},
								},
							},
						},
					},
				},
			},
		},
	}

	got := ConfigMapReferencesForDeployment(deployment)
	want := []ConfigMapReference{{Name: "shared", Optional: false}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("unexpected ConfigMap references: got %#v, want %#v", got, want)
	}
}
