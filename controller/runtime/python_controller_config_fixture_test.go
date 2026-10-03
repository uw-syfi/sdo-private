package runtime

import (
	"path/filepath"
	"testing"
)

// Cross-language ControllerConfig fixture (inverse of the Go contract fixtures
// in TestGoContractFixtures). The Python launcher writes the config the
// controller reads; tests/fixtures/sdo/contracts/python/controller_config.json
// is the launcher's real canonical-protojson output, pinned by
// tests/unit/sdo/contracts/test_python_controller_config_fixture.py.
//
// This test loads that exact committed file through the controller's own
// startup path (LoadControllerConfig does protojson's strict unmarshal +
// protovalidate) and the config->flags expansion, then asserts the flags the
// controller derives. If Python's encoder ever drifts from this parser, the
// build fails here instead of a controller pod failing to launch mid-run.
//
// Regenerate the fixture (Python side) with:
//
//	SDO_UPDATE_PYTHON_CONTRACT_FIXTURES=1 uv run --extra test pytest \
//	    tests/unit/sdo/contracts/test_python_controller_config_fixture.py

var pythonContractFixtureDir = filepath.Join("..", "..", "tests", "fixtures", "sdo", "contracts", "python")

func TestPythonControllerConfigFixtureLoadsAndExpands(t *testing.T) {
	path := filepath.Join(pythonContractFixtureDir, "controller_config.json")

	config, err := LoadControllerConfig(path)
	if err != nil {
		t.Fatalf("LoadControllerConfig(%s): %v\n"+
			"regenerate the fixture with SDO_UPDATE_PYTHON_CONTRACT_FIXTURES=1 and rerun the Python test", path, err)
	}

	args := controllerConfigToArgs(config)

	// The controller's real flag set must accept every flag the launcher's
	// config expands to (the drift guard reused from controller_config_test.go).
	assertFlagsDefined(t, args)

	// Space-form scalars and durations (durations in Go duration syntax).
	pairs := argPairs(args)
	wantValue := map[string]string{
		"--namespace":                    "hotel-reservation",
		"--control-namespace":            "hotel-reservation-sdo",
		"--app-root":                     "/workspace/application",
		"--application":                  "hotel-reservation",
		"--source-commit":                "abc1234def",
		"--deployed-commit":              "99887766aa",
		"--dispatcher":                   "/usr/bin/python3",
		"--dispatcher-mode":              "job",
		"--responder-image":              "sdo-responder:v1",
		"--repository-pvc":               "sdo-repository",
		"--repository-mount-path":        "/workspace",
		"--repository-pvc-subpath":       "repo",
		"--responder-credentials-secret": "sdo-codex-credentials",
		"--broker":                       "/usr/bin/python3",
		"--broker-worktree-root":         "/workspace/worktrees",
		"--response-timeout":             "30m0s",
		"--verification-timeout":         "2m0s",
		"--follow-up-cooldown":           "30s",
		"--duration":                     "1h0m0s",
		"--max-follow-ups":               "2",
		"--repair-policy":                "recorded-actions",
		"--lease-name":                   "sdo-controller",
		"--prober-binary":                "/workspace/.sdo-prober/0123456789abcdef/sdo-prober",
		"--prober-image":                 "sdo-controller:v1",
	}
	for flag, want := range wantValue {
		if got := pairs[flag]; got != want {
			t.Fatalf("%s: got %q want %q\nargs: %v", flag, got, want, args)
		}
	}

	// Repeated fields expand once per element as --flag=value.
	for _, flag := range []string{
		"--dispatcher-arg=-m",
		"--dispatcher-arg=sdo.agent_runtime.responder.job",
		"--broker-arg=-m",
		"--broker-arg=sdo.agent_runtime.responder.broker_cli",
		"--responder-env=SDO_MODEL=luna",
	} {
		if !contains(args, flag) {
			t.Fatalf("expected %q in args: %v", flag, args)
		}
	}

	// exit_after_closure true is a bare switch; restart stays off.
	if !contains(args, "--exit-after-closure") {
		t.Fatalf("expected --exit-after-closure in args: %v", args)
	}
	if contains(args, "--restart-after-closure") {
		t.Fatalf("did not expect --restart-after-closure in args: %v", args)
	}
}
