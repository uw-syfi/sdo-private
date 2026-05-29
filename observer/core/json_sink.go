package core

import (
	"context"
	"encoding/json"
	"fmt"
	"io"

	"sds.dev/observer/sdk"
)

type JSONSink struct {
	Writer io.Writer
}

func (s JSONSink) Emit(_ context.Context, finding sdk.Finding) error {
	if s.Writer == nil {
		return fmt.Errorf("writer is required")
	}
	encoder := json.NewEncoder(s.Writer)
	return encoder.Encode(finding)
}
