# Validator image plumbing: decisions

Branch `vic/fix/validator-image-plumbing` (from `main` `d6efc100`), 2026-10-02. Driver: the Tier 1 mini-stream's cold lifecycle ran the health judge's `sdo detector check` against `sdo-detector-validator:v0.1.0` (built 2026-09-28, no `Links` field in `controller/sdk/traffic/workload.go`). `workloads/links.yaml` was rejected as an unknown field, the judge dropped it, and the traffic link-reachability detector was never installed. A cold deny-all NetworkPolicy on `recommendation` then went undetected for 11 minutes.

## Root cause

- `sdo/agent_runtime/lifecycle/operational_memory.py` built `ContainerSandboxRunner()` with no image in three places; `sdo/operational_memory/sandbox.py` falls back to `SDO_VALIDATOR_IMAGE`, then to the shared `v0.1.0` tag.
- The judge's `sdo detector check` runs in a child of the agent CLI (a gateway command outside the Codex shell sandbox) and resolves its image the same way.
- Nothing on the host-side path received the adapter's `validator_image`; it only reached the controller install (`benchmarks/sregym/fastloop/cli.py`, `adapter/driver.py`). The link-probe canary worked around it with an env override in its own launcher (`link_probe_canary/RESULTS.md`, item 4).

## Decisions

| # | Decision | Alternatives | Why |
| --- | --- | --- | --- |
| 1 | `run_initial_lifecycle` and `reuse_initial_lifecycle_if_valid` take `validator_image: str \| None`. An injected `validator` still wins for the controller's own validations; otherwise `ContainerSandboxRunner(image=validator_image)`. | Add an `image` field to `OperationConfig` only; or keep the env-var override. | The image is already configured on `OperationConfig` and the adapter's `args.validator_image`; the lifecycle was the one consumer that never received it. The env override is per-launcher and was forgotten twice. |
| 2 | `run_initial_lifecycle` also exports the image as `SDO_VALIDATOR_IMAGE` for the call's scope (`validator_image_environment`, restored afterwards). | Pass an env mapping through `run_structured_turn` to the agent CLI. | The judge's self-check is a separate process the lifecycle does not construct. `run_structured_turn` already passes `{**os.environ, **extra_env}` to the agent CLI, so a scoped export reaches it with no change to `libs/agent_cli`. Costs: the export is process-global for the call; the lifecycle is sequential per process. An explicit env-mapping parameter would be cleaner and is the follow-up if lifecycles ever run concurrently in one process. |
| 3 | `ControllerDeploymentVerifier(validator_image=...)` and `operate()` pass `OperationConfig.validator_image`; the SREGym driver passes `args.validator_image` at all three lifecycle call sites. | Only the persistent-controller path. | One-shot and persistent flows share the defect. |
| 4 | `ContainerSandboxRunner.run` raises `StaleValidatorImageError` when a failed validation reports `unknown field "X"` and `X` is a `json:"X"` field in this checkout's `controller/sdk`. An unknown field the SDK does not define is still an ordinary authoring failure. | A label/fingerprint preflight comparing the image's SDK digest with the checkout; grep the image for the field before every run. | It is precise (no false stops for unrelated old images), needs no image rebuild, and costs nothing on passing runs. The message names the image, the field and the rebuild command, and says not to drop the field from the workload (the behaviour that hid the bug). A label preflight is stricter but requires changing `Dockerfile.validator` and `build_sdo_images.sh`, and would flag images that are merely older but compatible. Limit: detection needs the repo's `controller/sdk` on disk (true in a checkout; skipped when `sdo` is installed without it). The Kubernetes Job runner's image is always explicit (broker args) and is not checked. |
| 5 | `ControllerInstallConfig` and `OperationConfig` call `require_matching_image_tags`: `sdo-controller:<a>` with `sdo-detector-validator:<b>` and `a != b` is a `ValueError`. Digest references, untagged references and non-SDO image names are accepted. | Warn only; compare against the responder image too. | The images are built together from one checkout under one `SDO_IMAGE_TAG`; a private controller tag next to the shared validator tag is exactly the mismatch seen. A warning was already ignorable. The responder image does not run detector validation, so it is not compared. |
| 6 | Other `ContainerSandboxRunner()` construction sites: `sdo/operational_memory/validation.py:48` (`MemoryValidator` default) and `sdo/agent_runtime/responder/broker_cli.py:92` (healthy-baseline gate). Left on the env/default fallback. | Plumb an image through them too. | Found by grep only; I did not trace every caller. The production broker path selects `KubernetesJobSandboxRunner` with the explicit `--validator-image` broker argument, and these two are the container-runtime fallbacks. They get decision 4's staleness error for free. Follow-up if either is used host-side with a private tag. |

## Observation (not verified)

Every committed experiment TOML sets matching private tags for controller and validator, but before this change only the controller install used the validator tag. A host-side lifecycle on a private tag therefore validated against `v0.1.0` unless the launcher exported `SDO_VALIDATOR_IMAGE` (the link-probe canary did; I did not check the others). Results from earlier private-tag runs may have had link workloads dropped the same way.

## What the change does not do

- It does not run a cluster experiment or an LLM; the confirmation below is still open.
- It does not make a stale image impossible; it makes it loud (decision 4) and keeps configured images consistent (decisions 1, 3, 5).
- Existing lifecycle validation caches key on the validator image identity (`validation_identity`), so a corrected image does not reuse an old verdict.

## Confirming cluster run (not done)

1. Build all images under a fresh tag from this branch (`SDO_IMAGE_TAG=<tag> scripts/build_sdo_images.sh`) and pass that tag as controller, responder and validator image. Do not set `SDO_VALIDATOR_IMAGE`.
2. Cold lifecycle on hotel-reservation (`lifecycle-provenance.yaml` plus `.sdo/diagnostics/traffic/workloads/links.yaml`): assert `links.yaml` survives validation (no "unknown field" in the judge rounds) and the `traffic-links` detector exists.
3. Cold `network_policy_block` single: a `link-reachability.frontend.recommendation.<port>` finding within seconds of injection and a responder dispatched.
4. A healthy run for the same stream with no injection: count link-reachability findings (false positives were an open item in the link-probe canary).
5. Pass condition: 2 and 3 hold, 4 shows no findings over at least 10 minutes.
