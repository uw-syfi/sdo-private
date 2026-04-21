"""Shared SREGym library.

Covers both the **launcher side** (experiment + pipeline configs, runner
orchestration) and the **agent side** (conductor HTTP client, benchmark
result schema, MCP submission client, shared contract constants) of the
SREGym contract.  Any agent under ``sregym_agents/`` should depend on
this library rather than duplicating HTTP/schema code.
"""

from libs.sregym_lib.benchmark import (
    BenchmarkResult,
    Oracle,
    Stage,
)
from libs.sregym_lib.conductor import (
    get_api_base,
    get_app_info,
    get_current_stage,
    get_current_stage_sync,
    get_planned_stages,
    get_problem_id,
    poll_stage,
    poll_stage_sync,
    signal_cleanup,
    wait_for_stages_or_last_seen,
    wait_for_stages_or_last_seen_sync,
)
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
from libs.sregym_lib.mcp_client import submit_to_benchmark
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
from libs.sregym_lib.schema import (
    MAX_DIAGNOSIS_CANDIDATES,
    READY_STAGES,
    TERMINAL_STAGES,
)

__all__ = [
    "MAX_DIAGNOSIS_CANDIDATES",
    "READY_STAGES",
    "TERMINAL_STAGES",
    "BenchmarkResult",
    "ExperimentConfig",
    "Oracle",
    "PipelineConfig",
    "PipelineState",
    "RunnerEnv",
    "Stage",
    "StageConfig",
    "StageHooks",
    "StageState",
    "VariantConfig",
    "VariantOrder",
    "config_to_env",
    "config_to_main_args",
    "get_api_base",
    "get_app_info",
    "get_current_stage",
    "get_current_stage_sync",
    "get_planned_stages",
    "get_problem_id",
    "has_pipeline_state",
    "is_pipeline_config",
    "load_experiment_config",
    "load_pipeline_config",
    "merge_stage_config",
    "poll_stage",
    "poll_stage_sync",
    "read_pipeline_snapshot",
    "read_pipeline_state",
    "read_snapshot",
    "reset_stages_for_rerun",
    "resolve_config",
    "resolve_tasklist",
    "run_pipeline",
    "run_single_experiment",
    "signal_cleanup",
    "submit_to_benchmark",
    "variant_config_from_raw",
    "wait_for_stages_or_last_seen",
    "wait_for_stages_or_last_seen_sync",
    "write_pipeline_snapshot",
    "write_pipeline_state",
    "write_snapshot",
]
