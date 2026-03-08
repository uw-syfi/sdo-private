#!/usr/bin/env python3
"""End-to-end test of the DSPy-native operator against fleetcast."""

import os
import shutil
import sys
import tempfile
import time
import traceback

from app_operator_dspy.operator import DSPyOperator, configure_lm


def main():
    # --- Configuration ---
    source_app = os.path.join(os.path.dirname(__file__), "apps", "fleetcast")
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "allmos-487905")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")

    # Copy app to a temp dir so we don't pollute the repo
    work_dir = tempfile.mkdtemp(prefix="sds_dspy_e2e_")
    shutil.copytree(source_app, os.path.join(work_dir, "fleetcast"), dirs_exist_ok=False)
    repo_path = os.path.join(work_dir, "fleetcast")

    # Remap port 5000 → 5050 to avoid macOS AirPlay conflict
    compose_file = os.path.join(repo_path, "docker-compose.yml")
    with open(compose_file) as fh:
        content = fh.read()
    content = content.replace('"5000:5000"', '"5050:5000"')
    with open(compose_file, "w") as fh:
        fh.write(content)

    print(f"[setup] Working directory: {repo_path}")

    # --- Configure LM ---
    print(f"[setup] Configuring Vertex AI LM (project={project}, location={location})")
    configure_lm(
        "vertex_ai/gemini-2.5-pro",
        vertex_project=project,
        vertex_location=location,
    )

    # --- Run operator ---
    print("[run] Creating DSPyOperator...")
    operator = DSPyOperator()

    print(f"[run] Starting operator on {repo_path}")
    start = time.time()
    try:
        result = operator(
            repo_path=repo_path,
            max_deploy_attempts=3,
            monitor_checks=2,
        )
    except Exception as e:
        print(f"\n[ERROR] Operator failed with exception: {type(e).__name__}: {e}")
        traceback.print_exc()
        return 1

    elapsed = time.time() - start
    print(f"\n{'=' * 60}")
    print(f"[result] Completed in {elapsed:.1f}s")
    print(f"[result] Success: {result.success}")
    print(f"[result] Phase: {result.phase}")

    if result.success:
        print(f"[result] Monitor statuses: {result.statuses}")
    else:
        print(f"[result] Error: {getattr(result, 'error', 'N/A')}")

    # Show generated files
    sds_dir = os.path.join(repo_path, ".sds")
    if os.path.isdir(sds_dir):
        print(f"\n[artifacts] Files in {sds_dir}:")
        for root, _dirs, files in os.walk(sds_dir):
            for f in files:
                fpath = os.path.join(root, f)
                size = os.path.getsize(fpath)
                rel = os.path.relpath(fpath, sds_dir)
                print(f"  .sds/{rel} ({size} bytes)")

    # Print generated scripts
    for script in ["code_analysis.md", "deploy.sh", "health_check.sh"]:
        spath = os.path.join(sds_dir, script)
        if os.path.isfile(spath):
            with open(spath) as fh:
                content = fh.read()
            print(f"\n{'=' * 60}")
            print(f"[artifact] .sds/{script}:")
            print(f"{'=' * 60}")
            print(content[:2000])
            if len(content) > 2000:
                print(f"... ({len(content) - 2000} more chars)")

    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
