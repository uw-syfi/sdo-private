package runtime

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os/exec"
	"time"
)

type SubprocessIncidentBroker struct {
	Argv    []string
	Timeout time.Duration
}

func (b SubprocessIncidentBroker) PrepareIncident(ctx context.Context, incidentID string) (IncidentWorkspace, error) {
	var workspace IncidentWorkspace
	if err := b.invoke(ctx, map[string]any{"operation": "prepare", "incident_id": incidentID}, &workspace); err != nil {
		return IncidentWorkspace{}, err
	}
	return workspace, nil
}

func (b SubprocessIncidentBroker) ProcessClosure(ctx context.Context, closure IncidentClosure) (ClosureReceipt, error) {
	var receipt ClosureReceipt
	if err := b.invoke(ctx, map[string]any{"operation": "process", "closure": closure}, &receipt); err != nil {
		return ClosureReceipt{}, err
	}
	return receipt, nil
}

func (b SubprocessIncidentBroker) AcknowledgeClosure(ctx context.Context, receipt ClosureReceipt) error {
	var response struct {
		Acknowledged bool `json:"acknowledged"`
	}
	if err := b.invoke(ctx, map[string]any{"operation": "ack", "receipt": receipt}, &response); err != nil {
		return err
	}
	if !response.Acknowledged {
		return fmt.Errorf("broker did not acknowledge closure")
	}
	return nil
}

func (b SubprocessIncidentBroker) invoke(ctx context.Context, request any, response any) error {
	if len(b.Argv) == 0 || b.Argv[0] == "" {
		return fmt.Errorf("broker argv is required")
	}
	if b.Timeout <= 0 {
		return fmt.Errorf("broker timeout must be positive")
	}
	payload, err := json.Marshal(request)
	if err != nil {
		return fmt.Errorf("encode broker request: %w", err)
	}
	brokerCtx, cancel := context.WithTimeout(ctx, b.Timeout)
	defer cancel()
	command := exec.CommandContext(brokerCtx, b.Argv[0], b.Argv[1:]...)
	command.Stdin = bytes.NewReader(payload)
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	command.Stdout = &stdout
	command.Stderr = &stderr
	if err := command.Run(); err != nil {
		if brokerCtx.Err() != nil {
			return brokerCtx.Err()
		}
		return fmt.Errorf("broker exited unsuccessfully: %w: %s", err, stderr.String())
	}
	decoder := json.NewDecoder(&stdout)
	if err := decoder.Decode(response); err != nil {
		return fmt.Errorf("decode broker response: %w", err)
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		return fmt.Errorf("decode broker response: trailing output")
	}
	return nil
}
