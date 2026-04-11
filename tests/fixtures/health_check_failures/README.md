# Health Check Failure Replay Corpus

Each subdirectory is a fixture case derived from a real experiment failure.

## Case structure

```
<case-name>/
  repo/                   Minimal file tree representing the app repo
    cmd/<service>/        Source dirs with (or without) self-registration code
    services/<service>/
  health_check.sh         The generated health_check.sh that triggered the failure
  expected.toml           Expected classification outcome
```

## expected.toml schema

```toml
verdict = "invalid_false_negative"   # valid | invalid_false_negative | invalid_brittle_probe | invalid_service_discovery_expectation

# Services that should be flagged as invalid consul registration requirements
# (empty list means none expected)
invalid_consul_services = ["frontend"]
```

## Adding a new case

When a babysitting session reveals a new health-check failure pattern:

1. Create a new subdirectory named after the symptom (e.g., `consul_frontend_no_self_register`)
2. Add a minimal `repo/` tree that reproduces the evidence extraction
3. Copy the offending `health_check.sh`
4. Set `expected.toml` with the expected verdict
5. Run `pytest tests/unit/health_policy/test_replay_corpus.py` to verify
