"""Shared SREGym library — experiment/pipeline configs, runner, conductor comms.

Currently exports the launcher-side surface (experiment + pipeline configs and
the runner orchestration).  Conductor-side client code (status polling,
benchmark result schema, MCP submission) will migrate here in a follow-up.
"""

from libs.sregym_lib.experiment import (
    ExperimentConfig,
    RunnerEnv,
    VariantConfig,
    VariantOrder,
    config_to_env,
    config_to_main_args,
    load_experiment_config,
    read_snapshot,
    resolve_config,
    resolve_tasklist,
    variant_config_from_raw,
    write_snapshot,
)
from libs.sregym_lib.pipeline import (
    PipelineConfig,
    PipelineState,
    StageConfig,
    StageState,
    has_pipeline_state,
    is_pipeline_config,
    load_pipeline_config,
    merge_stage_config,
    read_pipeline_snapshot,
    read_pipeline_state,
    reset_stages_for_rerun,
    write_pipeline_snapshot,
    write_pipeline_state,
)
from libs.sregym_lib.runner import (
    StageHooks,
    run_pipeline,
    run_single_experiment,
)

__all__ = [
    "ExperimentConfig",
    "PipelineConfig",
    "PipelineState",
    "RunnerEnv",
    "StageConfig",
    "StageHooks",
    "StageState",
    "VariantConfig",
    "VariantOrder",
    "config_to_env",
    "config_to_main_args",
    "has_pipeline_state",
    "is_pipeline_config",
    "load_experiment_config",
    "load_pipeline_config",
    "merge_stage_config",
    "read_pipeline_snapshot",
    "read_pipeline_state",
    "read_snapshot",
    "resolve_config",
    "resolve_tasklist",
    "reset_stages_for_rerun",
    "run_pipeline",
    "run_single_experiment",
    "variant_config_from_raw",
    "write_pipeline_snapshot",
    "write_pipeline_state",
    "write_snapshot",
]
