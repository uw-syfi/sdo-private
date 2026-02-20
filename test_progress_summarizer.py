#!/usr/bin/env python3
"""Test script to verify ProgressSummarizer is working.

This creates a fake long-running deployment to test if the summarizer
triggers at the right times and records data properly.
"""

import logging
import time
from pathlib import Path
import tempfile
import sys

from app_operator.cli_agent.progress_summarizer import ProgressSummarizer
from app_operator.trajectory import TrajectoryRecorder, Phase

# Setup debug logging to see all the debug messages
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)


def fake_agent_generate(prompt: str, silent: bool, timeout: int) -> str:
    """Fake agent that returns a summary."""
    print(f"\n{'='*60}")
    print(f"AGENT CALLED (silent={silent}):")
    print(f"Prompt: {prompt[:200]}...")
    print(f"{'='*60}\n")

    # Return a fake summary in the expected format
    return "<output_msg>Deployment is running... building containers</output_msg>"


def test_progress_summarizer_standalone():
    """Test the summarizer in isolation."""
    print("\n" + "="*70)
    print("TEST 1: ProgressSummarizer Standalone")
    print("="*70 + "\n")

    # Create summarizer with short delays for testing
    summarizer = ProgressSummarizer(
        agent_generate_fn=fake_agent_generate,
        initial_delay=2.0,  # 2 seconds for testing
        summary_interval=3.0,  # 3 seconds between summaries
        recorder=None
    )

    # Start it
    summarizer.start()
    print("✓ Summarizer started at t=0s")

    # Simulate a 10-second process
    for t in range(11):
        time.sleep(1)
        elapsed = t + 1

        should = summarizer.should_summarize()
        print(f"t={elapsed}s: should_summarize={should}")

        if should:
            print("  → Calling summarizer.summarize()")
            summarizer.summarize(f"Fake output at {elapsed} seconds")

    print("\n✓ Test 1 complete\n")


def test_progress_summarizer_with_subprocess():
    """Test the summarizer integrated with SubprocessRunner."""
    print("\n" + "="*70)
    print("TEST 2: ProgressSummarizer with SubprocessRunner")
    print("="*70 + "\n")

    from app_operator.cli_agent.subprocess_runner import SubprocessRunner

    # Create a temp directory for testing
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # Create a fake deployment script that runs for 20 seconds
        script_path = tmpdir / "slow_deploy.sh"
        script_path.write_text("""#!/bin/bash
echo "Starting deployment..."
for i in {1..20}; do
    echo "Step $i: Building container..."
    sleep 1
done
echo "Deployment complete!"
""")
        script_path.chmod(0o755)

        # Create summarizer
        summarizer = ProgressSummarizer(
            agent_generate_fn=fake_agent_generate,
            initial_delay=5.0,  # 5 seconds
            summary_interval=5.0,  # Every 5 seconds
            recorder=None
        )

        # Create subprocess runner
        runner = SubprocessRunner(
            command=['bash', str(script_path)],
            cwd=str(tmpdir),
            timeout=30,
        )

        print("✓ Running 20-second deployment script...")
        print("  Expected: Summaries at ~5s, ~10s, ~15s\n")

        result = runner.run_with_progress_monitoring(summarizer)

        print("\n✓ Script completed:")
        print(f"  Exit code: {result['exit_code']}")
        print(f"  Success: {result['success']}")

    print("\n✓ Test 2 complete\n")


def test_progress_summarizer_with_trajectory():
    """Test if summarizer calls are recorded in trajectory."""
    print("\n" + "="*70)
    print("TEST 3: ProgressSummarizer with Trajectory Recording")
    print("="*70 + "\n")

    from app_operator.cli_agent.subprocess_runner import SubprocessRunner

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # Create trajectory recorder
        trajectories_dir = tmpdir / "trajectories"
        trajectories_dir.mkdir()

        recorder = TrajectoryRecorder(
            run_id="test-20260213-000000",
            trajectories_dir=trajectories_dir
        )

        # Start a deployment phase
        recorder.start_phase(Phase.DEPLOYMENT, {"attempt": 1, "max_attempts": 3})

        # Create a fake agent that uses the recorder
        def agent_with_recorder(prompt: str, silent: bool, timeout: int) -> str:
            print(f"\n{'='*60}")
            print("AGENT CALLED (with recorder):")
            print(f"Prompt length: {len(prompt)} chars")
            print(f"Silent: {silent}")
            print(f"{'='*60}\n")

            # Simulate agent recording (would normally happen in agent.generate)
            recorder.add_message("user", prompt)
            recorder.add_message("assistant", "<output_msg>Building containers...</output_msg>")

            return "<output_msg>Building containers...</output_msg>"

        # Create summarizer with recorder
        summarizer = ProgressSummarizer(
            agent_generate_fn=agent_with_recorder,
            initial_delay=3.0,
            summary_interval=4.0,
            recorder=recorder
        )

        # Create a script
        script_path = tmpdir / "deploy.sh"
        script_path.write_text("""#!/bin/bash
echo "Deploying..."
for i in {1..12}; do
    echo "Step $i"
    sleep 1
done
echo "Done!"
""")
        script_path.chmod(0o755)

        # Run with subprocess runner
        runner = SubprocessRunner(
            command=['bash', str(script_path)],
            cwd=str(tmpdir),
            timeout=20,
        )

        print("✓ Running 12-second deployment with trajectory recording...")
        runner.run_with_progress_monitoring(summarizer)

        # End the phase
        recorder.end_phase()

        # Finalize trajectory
        traj_file = recorder.finalize("completed")

        print(f"\n✓ Trajectory saved to: {traj_file}")

        # Check if trajectory has summarize calls
        import json
        with open(traj_file) as f:
            traj = json.load(f)

        deployment_entries = traj.get('deployment', [])
        print(f"\n✓ Deployment phase has {len(deployment_entries)} entry/entries")

        for entry in deployment_entries:
            messages = entry.get('messages', [])
            print(f"  - Call {entry['call_id']}: {len(messages)} messages")

            # Check for deployer_summarize prompts
            user_messages = [m for m in messages if m.get('role') == 'user']
            for msg in user_messages:
                content = msg.get('content', '')
                if 'summarize' in content.lower() or 'output_msg' in content.lower():
                    print("    ✓ FOUND deployer_summarize call!")
                    print(f"      Content: {content[:100]}...")

    print("\n✓ Test 3 complete\n")


if __name__ == '__main__':
    # Run all tests
    try:
        test_progress_summarizer_standalone()
        test_progress_summarizer_with_subprocess()
        test_progress_summarizer_with_trajectory()

        print("\n" + "="*70)
        print("ALL TESTS PASSED!")
        print("="*70 + "\n")

    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
