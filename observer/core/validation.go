package core

import (
	"fmt"
	"strings"

	"sds.dev/observer/sdk"
)

func ValidateDetectors(detectors []sdk.Detector) error {
	seen := make(map[string]struct{}, len(detectors))
	for index, detector := range detectors {
		if detector == nil {
			return fmt.Errorf("detector %d is nil", index)
		}
		spec := detector.Spec()
		id := strings.TrimSpace(spec.ID)
		if id == "" {
			return fmt.Errorf("detector %d has empty id", index)
		}
		if _, ok := seen[id]; ok {
			return fmt.Errorf("duplicate detector id %q", id)
		}
		seen[id] = struct{}{}
	}
	return nil
}
