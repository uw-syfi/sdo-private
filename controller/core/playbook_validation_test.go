package core

import (
	"os"
	"path/filepath"
	"testing"

	"sdo.dev/controller/sdk"
)

func TestValidateFindingPlaybooksAcceptsCanonicalMemory(t *testing.T) {
	appRoot := t.TempDir()
	paths := []string{
		".sdo/playbooks/missing-configmap/README.md",
		".sdo/playbooks/service-endpoints/README.md",
	}
	for _, path := range paths {
		fullPath := filepath.Join(appRoot, filepath.FromSlash(path))
		if err := os.MkdirAll(filepath.Dir(fullPath), 0o755); err != nil {
			t.Fatalf("create playbook directory: %v", err)
		}
		if err := os.WriteFile(fullPath, []byte("# playbook\n"), 0o644); err != nil {
			t.Fatalf("write playbook: %v", err)
		}
	}

	finding := sdk.Finding{RuleID: "memory-paths", Playbooks: paths}
	if err := ValidateFindingPlaybooks(finding, appRoot); err != nil {
		t.Fatalf("validate canonical playbooks: %v", err)
	}
}

func TestValidateFindingPlaybooksRejectsNonMemoryPath(t *testing.T) {
	appRoot := t.TempDir()
	path := "docs/playbook.md"
	fullPath := filepath.Join(appRoot, path)
	if err := os.MkdirAll(filepath.Dir(fullPath), 0o755); err != nil {
		t.Fatalf("create docs directory: %v", err)
	}
	if err := os.WriteFile(fullPath, []byte("# not operational memory\n"), 0o644); err != nil {
		t.Fatalf("write document: %v", err)
	}

	err := ValidateFindingPlaybooks(sdk.Finding{RuleID: "outside", Playbooks: []string{path}}, appRoot)
	if err == nil {
		t.Fatal("expected non-memory path error")
	}
}
