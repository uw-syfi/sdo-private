package core

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"sdo.dev/controller/sdk"
)

type PlaybookValidatingSink struct {
	Inner   Sink
	AppRoot string
}

func (s PlaybookValidatingSink) Emit(ctx context.Context, finding sdk.Finding) error {
	if s.Inner == nil {
		return fmt.Errorf("inner sink is required")
	}
	if err := ValidateFindingPlaybooks(finding, s.AppRoot); err != nil {
		return err
	}
	return s.Inner.Emit(ctx, finding)
}

func ValidateFindingPlaybooks(finding sdk.Finding, appRoot string) error {
	if appRoot == "" {
		return nil
	}
	root, err := filepath.Abs(appRoot)
	if err != nil {
		return fmt.Errorf("resolve app root: %w", err)
	}
	seen := make(map[string]struct{}, len(finding.Playbooks))
	for _, playbook := range finding.Playbooks {
		if err := validatePlaybookPath(root, playbook); err != nil {
			return fmt.Errorf("finding %q playbook %q: %w", finding.RuleID, playbook, err)
		}
		clean := filepath.ToSlash(filepath.Clean(playbook))
		if _, ok := seen[clean]; ok {
			return fmt.Errorf("finding %q duplicate playbook %q", finding.RuleID, playbook)
		}
		seen[clean] = struct{}{}
	}
	return nil
}

func validatePlaybookPath(appRoot string, playbook string) error {
	if strings.TrimSpace(playbook) == "" {
		return fmt.Errorf("path is required")
	}
	if filepath.IsAbs(playbook) {
		return fmt.Errorf("path must be relative")
	}
	clean := filepath.ToSlash(filepath.Clean(playbook))
	if clean == "." || strings.HasPrefix(clean, "../") || clean == ".." {
		return fmt.Errorf("path must stay inside app root")
	}
	canonical := clean == ".sdo/playbooks" || strings.HasPrefix(clean, ".sdo/playbooks/")
	if !canonical {
		return fmt.Errorf("path must be under .sdo/playbooks")
	}
	fullPath := filepath.Join(appRoot, filepath.FromSlash(clean))
	rel, err := filepath.Rel(appRoot, fullPath)
	if err != nil {
		return fmt.Errorf("resolve path: %w", err)
	}
	if rel == ".." || strings.HasPrefix(filepath.ToSlash(rel), "../") {
		return fmt.Errorf("path must stay inside app root")
	}
	info, err := os.Stat(fullPath)
	if err != nil {
		if os.IsNotExist(err) {
			return fmt.Errorf("file does not exist")
		}
		return fmt.Errorf("stat file: %w", err)
	}
	if info.IsDir() {
		return fmt.Errorf("path must reference a file")
	}
	return nil
}
