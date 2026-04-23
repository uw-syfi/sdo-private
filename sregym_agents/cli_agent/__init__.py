"""SREGym CLI agent package."""

from libs.sregym_lib import NOOP_EXP_STAGE_LIFECYCLE, ExpStageLifecycle


def get_exp_stage_lifecycle() -> ExpStageLifecycle:
    return NOOP_EXP_STAGE_LIFECYCLE


__all__ = ["get_exp_stage_lifecycle"]
