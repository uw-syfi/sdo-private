package runtime

import (
	"bufio"
	"encoding/json"
	"os"
	"path/filepath"

	"sdo.dev/controller/sdk"
)

const maxRelevantOutcomes = 3

type outcomeMemoryRecord struct {
	IncidentID          string                `json:"incident_id"`
	SourceCommit        string                `json:"source_commit"`
	Classification      string                `json:"classification"`
	Findings            []sdk.Finding         `json:"findings"`
	ConfirmedRootCauses []ConfirmedRootCause  `json:"confirmed_root_causes"`
	AppliedPlaybooks    []string              `json:"applied_playbooks"`
	RepairActions       []RepairActionReceipt `json:"repair_actions"`
}

func relevantOutcomeEvidence(repository string, findings []sdk.Finding, sourceCommit string) []PriorOutcomeEvidence {
	file, err := os.Open(filepath.Join(repository, ".sdo", "outcomes.jsonl"))
	if err != nil {
		return nil
	}
	defer file.Close()

	records := make([]outcomeMemoryRecord, 0)
	scanner := bufio.NewScanner(file)
	scanner.Buffer(make([]byte, 64*1024), 4*1024*1024)
	for scanner.Scan() {
		var record outcomeMemoryRecord
		if json.Unmarshal(scanner.Bytes(), &record) == nil && record.Classification == "success" {
			records = append(records, record)
		}
	}

	evidence := make([]PriorOutcomeEvidence, 0, maxRelevantOutcomes)
	for index := len(records) - 1; index >= 0 && len(evidence) < maxRelevantOutcomes; index-- {
		record := records[index]
		match := outcomeMatch(record.Findings, findings)
		if match == "" {
			continue
		}
		rootCauses := make([]string, 0, len(record.ConfirmedRootCauses))
		for _, cause := range record.ConfirmedRootCauses {
			rootCauses = append(rootCauses, cause.Summary)
		}
		actions := make([]string, 0, len(record.RepairActions))
		for _, action := range record.RepairActions {
			if action.Success {
				actions = append(actions, action.Summary)
			}
		}
		evidence = append(evidence, PriorOutcomeEvidence{
			IncidentID: record.IncidentID, MatchReason: match, RootCauseSummaries: rootCauses,
			RepairActionSummaries: actions, AppliedPlaybooks: record.AppliedPlaybooks,
			SourceCommit: record.SourceCommit, ExactSourceMatch: record.SourceCommit == sourceCommit,
		})
	}
	return evidence
}

func outcomeMatch(previous []sdk.Finding, current []sdk.Finding) string {
	for _, left := range previous {
		for _, right := range current {
			if left.DetectorID == right.DetectorID && left.Fingerprint != "" && left.Fingerprint == right.Fingerprint {
				return "exact-fingerprint"
			}
		}
	}
	for _, left := range previous {
		for _, right := range current {
			if left.DetectorID == right.DetectorID && left.RuleID == right.RuleID &&
				left.PrimaryResource.Kind == right.PrimaryResource.Kind {
				return "detector-rule-resource-kind"
			}
		}
	}
	return ""
}
