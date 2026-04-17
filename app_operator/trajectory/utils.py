"""Utility functions for working with trajectory data.

This module provides helpers for extracting information from trajectory
dictionaries, decoupled from any specific metric or optimization framework.
"""

import json
from typing import Any

from app_operator.logger import logger


def extract_rlm_statistics_from_trajectory(trajectory_dict: dict[str, Any]) -> dict[str, Any]:
    """Extract RLM statistics from a trajectory dictionary.

    Args:
        trajectory_dict: Trajectory JSON loaded as dict

    Returns:
        Dictionary of RLM statistics
    """
    stats: dict[str, Any] = {
        "total_calls": 0,
        "code_executions": 0,
        "recursive_calls": 0,
        "total_tokens_saved": 0,
        "max_depth_reached": 0,
        "metadata_feedback_count": 0,
        "feedback_turns": 0,
        "finalization_type": "final_answer",
    }

    # Look for RLM-specific messages in deployment phase
    deployment_convos = trajectory_dict.get("deployment", [])

    found_json_stats = False

    for convo in deployment_convos:
        messages = convo.get("messages", [])

        for msg in messages:
            content = msg.get("content", "")

            # Check if it's an RLM statistics message
            if "RLM Statistics:" in content:
                try:
                    # Extract JSON from message
                    json_start = content.find("{")
                    json_str = content[json_start:]
                    extracted_stats = json.loads(json_str)
                    stats.update(extracted_stats)
                    found_json_stats = True
                except (json.JSONDecodeError, ValueError) as e:
                    logger.warning(f"Failed to parse RLM statistics: {e}")

            # Count RLM action messages only if no JSON stats were found
            elif not found_json_stats:
                if "[RLM execute_code" in content:
                    stats["code_executions"] += 1
                    stats["total_calls"] += 1
                elif "[RLM recursive_call" in content:
                    stats["recursive_calls"] += 1
                    stats["total_calls"] += 1
                if "stored in `last_" in content:
                    stats["metadata_feedback_count"] += 1

    return stats
