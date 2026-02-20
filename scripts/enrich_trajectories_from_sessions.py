#!/usr/bin/env python3
"""Enrich trajectory data with full conversation details from Gemini sessions.

This script links trajectory calls to Gemini session files and extracts:
- Complete user prompts (not just kwargs)
- Full tool call sequences
- Agent responses and reasoning
- Token usage statistics

This enriched data can be used for better prompt optimization.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional
from collections import defaultdict


def parse_time(time_str: str) -> Optional[datetime]:
    """Parse both trajectory and session time formats."""
    try:
        # Trajectory format: "2026-02-12 03:08:23"
        return datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            # Session format: "2026-02-12T03:08:23.000Z"
            return datetime.strptime(time_str[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None


def identify_prompt_type(user_message: str) -> str:
    """Identify the prompt type from user message content."""
    content_lower = user_message.lower()

    # deployer prompts
    if 'summarize' in content_lower or 'output_msg' in content_lower:
        return 'deployer_summarize'
    if 'deployment has failed' in content_lower or 'analyze the error and fix' in content_lower:
        return 'deployer_fix_error'
    if 'generate' in content_lower and 'deploy.sh' in content_lower:
        return 'deployer_generate_deploy_script'
    if 'generate' in content_lower and 'health_check.sh' in content_lower:
        return 'deployer_generate_health_check'

    # code_analyzer prompts
    if 'code analysis agent' in content_lower or 'analyze the codebase' in content_lower:
        return 'code_analyzer_user'

    # monitor prompts
    if 'analyze.*health' in content_lower or 'health check.*result' in content_lower:
        return 'monitor_analyze_health'

    return 'unknown'


def extract_prompt_kwargs_from_session(session: Dict, prompt_type: str) -> Dict[str, Any]:
    """Extract prompt kwargs from session messages based on prompt type.

    This reverse-engineers the kwargs that were used to render the prompt
    by analyzing the user message content.
    """
    kwargs = {}

    # Get first user message
    first_user = next((m for m in session['messages'] if m['type'] == 'user'), None)
    if not first_user:
        return kwargs

    content = first_user['content']

    # Extract kwargs based on prompt type
    if prompt_type == 'deployer_fix_error':
        # Look for error context, attempt number, etc.
        if 'attempt' in content.lower():
            # Try to extract attempt number
            import re
            match = re.search(r'attempt[:\s]+(\d+)', content.lower())
            if match:
                kwargs['attempt'] = int(match.group(1))

        # The full content is the rendered prompt
        kwargs['_rendered_prompt'] = content

    elif prompt_type == 'deployer_summarize':
        # The content after the instruction is the output_snippet
        kwargs['_rendered_prompt'] = content

    elif prompt_type == 'code_analyzer_user':
        kwargs['_rendered_prompt'] = content

    # Add common fields
    kwargs['_full_conversation'] = session['messages']
    kwargs['_token_usage'] = {
        'total': sum(m.get('tokens', {}).get('total', 0)
                    for m in session['messages'] if 'tokens' in m)
    }

    return kwargs


def link_sessions_to_trajectory(traj_path: Path) -> Dict[int, List[Dict]]:
    """Link Gemini sessions to trajectory calls.

    Returns:
        Dict mapping call_id to list of enriched session data
    """
    with open(traj_path) as f:
        traj = json.load(f)

    run_id = traj['metadata']['run_id']
    sessions_dir = traj_path.parent / 'gemini_sessions' / run_id

    if not sessions_dir.exists():
        print(f"  No Gemini sessions found for {traj_path}")
        return {}

    # Load all sessions
    sessions = []
    for session_file in sessions_dir.glob('*.json'):
        with open(session_file) as f:
            session = json.load(f)
            session['_file'] = session_file.name
            sessions.append(session)

    # Sort by start time
    sessions.sort(key=lambda s: s['startTime'])

    # Map sessions to call_ids
    call_sessions = defaultdict(list)

    for session in sessions:
        session_start = parse_time(session['startTime'])

        # Find the trajectory call this session belongs to
        for call in traj.get('calls', []):
            call_start = parse_time(call['start_time'])
            call_end = parse_time(call['end_time']) if call.get('end_time') else None

            # Check if session starts within call window
            if call_start and session_start and call_start <= session_start:
                if not call_end or session_start <= call_end:
                    # Get first user message
                    first_user = next((m for m in session['messages'] if m['type'] == 'user'), None)
                    if first_user:
                        prompt_type = identify_prompt_type(first_user['content'])
                        kwargs = extract_prompt_kwargs_from_session(session, prompt_type)

                        enriched = {
                            'session_file': session['_file'],
                            'session_start': session['startTime'],
                            'prompt_type': prompt_type,
                            'prompt_kwargs': kwargs,
                            'rendered_prompt': first_user['content'],
                            'messages': session['messages'],
                            'num_messages': len(session['messages']),
                        }

                        call_sessions[call['call_id']].append(enriched)
                    break

    return dict(call_sessions)


def create_enriched_trajectory(traj_path: Path, output_path: Path):
    """Create an enriched trajectory file with full session data."""

    with open(traj_path) as f:
        traj = json.load(f)

    # Link sessions
    call_sessions = link_sessions_to_trajectory(traj_path)

    # Add enriched data to trajectory
    enriched_traj = traj.copy()
    enriched_traj['_enriched'] = True
    enriched_traj['_enrichment_timestamp'] = datetime.now().isoformat()

    # Add session data to each phase
    for phase_key in ['exploration', 'script_generation', 'deployment', 'monitoring']:
        if phase_key not in enriched_traj:
            continue

        for entry in enriched_traj[phase_key]:
            call_id = entry.get('call_id')
            if call_id and call_id in call_sessions:
                # Add enriched sessions for this call
                entry['_sessions'] = call_sessions[call_id]

                # Extract all prompt types used in this call
                entry['_prompt_types'] = list(set(
                    s['prompt_type'] for s in call_sessions[call_id]
                ))

    # Write enriched trajectory
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(enriched_traj, f, indent=2)

    # Print stats
    total_sessions = sum(len(sessions) for sessions in call_sessions.values())
    prompt_types = defaultdict(int)
    for sessions in call_sessions.values():
        for s in sessions:
            prompt_types[s['prompt_type']] += 1

    print(f"  Linked {total_sessions} sessions to {len(call_sessions)} calls")
    print(f"  Prompt types: {dict(prompt_types)}")

    return enriched_traj


def main():
    """Enrich all trajectory files in opt2 and opt3."""

    import argparse
    parser = argparse.ArgumentParser(description='Enrich trajectories with Gemini session data')
    parser.add_argument('--input-dirs', nargs='+', default=['opt2', 'opt3'],
                       help='Directories containing trajectories')
    parser.add_argument('--output-dir', default='enriched_trajectories',
                       help='Output directory for enriched trajectories')
    args = parser.parse_args()

    output_base = Path(args.output_dir)

    for opt_dir in args.input_dirs:
        opt_path = Path(opt_dir)
        if not opt_path.exists():
            print(f"Skipping {opt_dir} (not found)")
            continue

        # Find all trajectory files
        traj_files = list(opt_path.glob('**/.sds/trajectories/trajectory_*.json'))

        print(f"\n{opt_dir}: Found {len(traj_files)} trajectory files")

        for traj_file in traj_files:
            app_name = traj_file.parts[-4]  # Extract app name from path
            print(f"\n  Processing {app_name}...")

            # Create output path
            output_path = output_base / opt_dir / app_name / 'enriched_trajectory.json'

            try:
                create_enriched_trajectory(traj_file, output_path)
                print(f"  ✓ Wrote {output_path}")
            except Exception as e:
                print(f"  ✗ Error: {e}")


if __name__ == '__main__':
    main()
