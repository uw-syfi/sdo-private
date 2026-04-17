"""App-operator logger utilities.

The configured loguru singleton lives in `libs.sds_core.logger`; this module
re-exports it for existing call sites and adds the operator-specific
`attach_ui_sink()` helper used to pipe log output into the TUI.
"""

from typing import TYPE_CHECKING

from libs.sds_core.logger import formatter, logger, setup_logger

if TYPE_CHECKING:
    from loguru import Message

    from app_operator.core.ui_protocol import OperatorUI


def attach_ui_sink(ui: "OperatorUI", replace: bool = False) -> None:
    """Attach the logger to the UI sink."""
    if replace:
        logger.remove()

    def sink(message: "Message") -> None:
        record = message.record
        text = record["message"]
        level = record["level"].name.lower()
        ui.log(text, level)

    logger.add(sink, format="{message}", level="INFO")


__all__ = ["attach_ui_sink", "formatter", "logger", "setup_logger"]
