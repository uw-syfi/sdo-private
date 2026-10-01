# Source-repair manifest guard: decisions

## Problem

In persistent-workspace mode a responder's source repair reached the operational branch as a Kubernetes manifest pasted from the live patched object. It had a duplicated `imagePullPolicy` key and a cluster-specific image tag. The next `kubectl apply -k` failed ("mapping key imagePullPolicy already defined"), so the next incident could not deploy.

## Decision

`CommitBroker.commit_proposal` now calls `sdo.operational_memory.manifest_guard.validate_yaml_manifests` on every changed non-`.sdo/` path before the configurable `ProposalValidator`. The check runs unconditionally, so it does not depend on which validation commands a deployment configures. It rejects the proposal with `MemoryValidationError`, naming the file, the key, and the line, when a changed `.yaml`/`.yml` file:

1. has a duplicate mapping key at any nesting level in any document of a multi-document stream, or
2. does not parse as YAML.

The rejection is rolled back like any other invalid proposal, and the message tells the responder to edit the source manifest instead of pasting the live object.

## Deliberate limits

- Deleted files, non-YAML files, and files containing `{{` (Helm/Go templates, not plain YAML) are skipped. Skipping templates avoids false rejections. The cost is that a templated file is unchecked.
- The guard is syntax-only and has no application-specific knowledge. It does not validate Kubernetes schemas; `kubectl --dry-run` would need a cluster and is out of scope.
- Only the broker's source-repair path is guarded. `.sdo/` memory has its own validator.

## Cluster-specific image tags: not rejected

Rejecting or warning on hard-coded image tags was considered and left out:

- Generic detection is unreliable. A pinned tag (`app:1.4.2`, a digest) is often the correct repair, for example rolling back a bad image. A local-registry or `sha-` tag cannot be told apart from a legitimate pin without knowing the deployment's conventions, which would be problem-specific.
- The broker has no warning channel; a warning nobody reads adds no protection, and a hard rejection would block valid repairs.
- The observed failure that broke deployment was the duplicate key. A stale tag degrades a later deploy but does not block `kubectl apply`.

If tag drift proves costly, the better lever is the responder prompt (prefer editing the existing manifest field over pasting the live object), not a broker rule. Revisit with evidence.

## Tests

- `tests/unit/sdo/operational_memory/test_manifest_guard.py`: valid multi-document file, duplicate key (message content), duplicate in a later document, unparseable YAML, skipped files, and image tags staying accepted.
- `tests/unit/sdo/operational_memory/test_memory.py`: the broker rejects a proposal with a duplicate key and leaves the target branch unchanged.
