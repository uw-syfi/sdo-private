"""Foundation helpers for app_operator.

Covers config, logging, types, exceptions, filesystem, validation, constants,
UI protocol, and command validation. Public surface is curated here —
importers should use `from app_operator.core import X` rather than reaching
into submodules.
"""

from app_operator.core.command_validation import (
    DangerousCommandError,
    validate_command,
)
from app_operator.core.config import (
    AgentConfig,
    Config,
    DeploymentConfig,
    DSPyAutoRollbackConfig,
    DSPyConfig,
    DSPyOptimizationConfig,
    FaultInjectionConfig,
    FeaturesConfig,
    GEPAConfig,
    OperatorConfig,
    OperatorPhaseConfig,
    RLMConfig,
    RuntimeConfig,
    load_config,
    qualify_model_for_litellm,
)
from app_operator.core.constants import (
    COMMAND_EXEC_TIMEOUT_SECS,
    DEPLOYMENT_PROGRESS_FILENAME,
    PROCESS_CLEANUP_TIMEOUT_SECS,
    PROCESS_TERM_WAIT_TIMEOUT_SECS,
    PROGRESS_INITIAL_DELAY_SECS,
    PROGRESS_SUMMARY_INTERVAL_SECS,
    THREAD_JOIN_TIMEOUT_SECS,
)
from app_operator.core.exceptions import (
    AgentError,
    ConfigurationError,
    DeploymentError,
    FileSystemError,
    MonitoringError,
    ProcessError,
    SdsOperatorError,
    UnrecognizedFieldError,
    UnrecognizedSectionError,
)
from app_operator.core.filesystem import (
    FileSystemInterface,
    InMemoryFilesystem,
    RealFilesystem,
)
from app_operator.core.logger import attach_ui_sink, formatter, logger, setup_logger
from app_operator.core.types import (
    CommandResult,
    ConversationEntry,
    HealthVerdict,
    TrajectoryCallRecord,
)
from app_operator.core.ui_protocol import NullOperatorUI, OperatorUI
from app_operator.core.validation import (
    validate_dataclass_fields,
    validate_field,
    validate_in,
    validate_non_empty_str,
    validate_non_negative,
    validate_positive,
    validate_range,
    validate_type,
)

__all__ = [
    "AgentConfig",
    "AgentError",
    "COMMAND_EXEC_TIMEOUT_SECS",
    "CommandResult",
    "Config",
    "ConfigurationError",
    "ConversationEntry",
    "DangerousCommandError",
    "DSPyAutoRollbackConfig",
    "DSPyConfig",
    "DSPyOptimizationConfig",
    "DEPLOYMENT_PROGRESS_FILENAME",
    "DeploymentConfig",
    "DeploymentError",
    "FaultInjectionConfig",
    "FeaturesConfig",
    "FileSystemError",
    "FileSystemInterface",
    "GEPAConfig",
    "HealthVerdict",
    "InMemoryFilesystem",
    "MonitoringError",
    "NullOperatorUI",
    "OperatorConfig",
    "OperatorPhaseConfig",
    "OperatorUI",
    "PROCESS_CLEANUP_TIMEOUT_SECS",
    "PROCESS_TERM_WAIT_TIMEOUT_SECS",
    "PROGRESS_INITIAL_DELAY_SECS",
    "PROGRESS_SUMMARY_INTERVAL_SECS",
    "ProcessError",
    "RLMConfig",
    "RealFilesystem",
    "RuntimeConfig",
    "SdsOperatorError",
    "THREAD_JOIN_TIMEOUT_SECS",
    "TrajectoryCallRecord",
    "UnrecognizedFieldError",
    "UnrecognizedSectionError",
    "attach_ui_sink",
    "formatter",
    "load_config",
    "logger",
    "qualify_model_for_litellm",
    "setup_logger",
    "validate_command",
    "validate_dataclass_fields",
    "validate_field",
    "validate_in",
    "validate_non_empty_str",
    "validate_non_negative",
    "validate_positive",
    "validate_range",
    "validate_type",
]
