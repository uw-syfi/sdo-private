import datetime

from buf.validate import validate_pb2 as _validate_pb2
from google.protobuf import duration_pb2 as _duration_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ControllerConfig(_message.Message):
    __slots__ = ("namespace", "control_namespace", "app_root", "application", "source_commit", "deployed_commit", "dispatcher", "dispatcher_mode", "dispatcher_args", "responder_image", "repository_pvc", "repository_mount_path", "repository_pvc_subpath", "responder_credentials_secret", "responder_env", "broker", "broker_worktree_root", "broker_args", "firing_telemetry_path", "duration", "response_timeout", "verification_timeout", "max_follow_ups", "follow_up_cooldown", "repair_policy", "lease_name", "lease_duration", "identity", "exit_after_closure", "restart_after_closure", "maintenance_configmap", "maintenance_poll_interval", "resume_sync_timeout", "synthetic_traffic", "synthetic_traffic_warmup", "prober_binary", "prober_image", "prober_url", "clean_responder_helpers", "state_baseline")
    NAMESPACE_FIELD_NUMBER: _ClassVar[int]
    CONTROL_NAMESPACE_FIELD_NUMBER: _ClassVar[int]
    APP_ROOT_FIELD_NUMBER: _ClassVar[int]
    APPLICATION_FIELD_NUMBER: _ClassVar[int]
    SOURCE_COMMIT_FIELD_NUMBER: _ClassVar[int]
    DEPLOYED_COMMIT_FIELD_NUMBER: _ClassVar[int]
    DISPATCHER_FIELD_NUMBER: _ClassVar[int]
    DISPATCHER_MODE_FIELD_NUMBER: _ClassVar[int]
    DISPATCHER_ARGS_FIELD_NUMBER: _ClassVar[int]
    RESPONDER_IMAGE_FIELD_NUMBER: _ClassVar[int]
    REPOSITORY_PVC_FIELD_NUMBER: _ClassVar[int]
    REPOSITORY_MOUNT_PATH_FIELD_NUMBER: _ClassVar[int]
    REPOSITORY_PVC_SUBPATH_FIELD_NUMBER: _ClassVar[int]
    RESPONDER_CREDENTIALS_SECRET_FIELD_NUMBER: _ClassVar[int]
    RESPONDER_ENV_FIELD_NUMBER: _ClassVar[int]
    BROKER_FIELD_NUMBER: _ClassVar[int]
    BROKER_WORKTREE_ROOT_FIELD_NUMBER: _ClassVar[int]
    BROKER_ARGS_FIELD_NUMBER: _ClassVar[int]
    FIRING_TELEMETRY_PATH_FIELD_NUMBER: _ClassVar[int]
    DURATION_FIELD_NUMBER: _ClassVar[int]
    RESPONSE_TIMEOUT_FIELD_NUMBER: _ClassVar[int]
    VERIFICATION_TIMEOUT_FIELD_NUMBER: _ClassVar[int]
    MAX_FOLLOW_UPS_FIELD_NUMBER: _ClassVar[int]
    FOLLOW_UP_COOLDOWN_FIELD_NUMBER: _ClassVar[int]
    REPAIR_POLICY_FIELD_NUMBER: _ClassVar[int]
    LEASE_NAME_FIELD_NUMBER: _ClassVar[int]
    LEASE_DURATION_FIELD_NUMBER: _ClassVar[int]
    IDENTITY_FIELD_NUMBER: _ClassVar[int]
    EXIT_AFTER_CLOSURE_FIELD_NUMBER: _ClassVar[int]
    RESTART_AFTER_CLOSURE_FIELD_NUMBER: _ClassVar[int]
    MAINTENANCE_CONFIGMAP_FIELD_NUMBER: _ClassVar[int]
    MAINTENANCE_POLL_INTERVAL_FIELD_NUMBER: _ClassVar[int]
    RESUME_SYNC_TIMEOUT_FIELD_NUMBER: _ClassVar[int]
    SYNTHETIC_TRAFFIC_FIELD_NUMBER: _ClassVar[int]
    SYNTHETIC_TRAFFIC_WARMUP_FIELD_NUMBER: _ClassVar[int]
    PROBER_BINARY_FIELD_NUMBER: _ClassVar[int]
    PROBER_IMAGE_FIELD_NUMBER: _ClassVar[int]
    PROBER_URL_FIELD_NUMBER: _ClassVar[int]
    CLEAN_RESPONDER_HELPERS_FIELD_NUMBER: _ClassVar[int]
    STATE_BASELINE_FIELD_NUMBER: _ClassVar[int]
    namespace: str
    control_namespace: str
    app_root: str
    application: str
    source_commit: str
    deployed_commit: str
    dispatcher: str
    dispatcher_mode: str
    dispatcher_args: _containers.RepeatedScalarFieldContainer[str]
    responder_image: str
    repository_pvc: str
    repository_mount_path: str
    repository_pvc_subpath: str
    responder_credentials_secret: str
    responder_env: _containers.RepeatedScalarFieldContainer[str]
    broker: str
    broker_worktree_root: str
    broker_args: _containers.RepeatedScalarFieldContainer[str]
    firing_telemetry_path: str
    duration: _duration_pb2.Duration
    response_timeout: _duration_pb2.Duration
    verification_timeout: _duration_pb2.Duration
    max_follow_ups: int
    follow_up_cooldown: _duration_pb2.Duration
    repair_policy: str
    lease_name: str
    lease_duration: _duration_pb2.Duration
    identity: str
    exit_after_closure: bool
    restart_after_closure: bool
    maintenance_configmap: str
    maintenance_poll_interval: _duration_pb2.Duration
    resume_sync_timeout: _duration_pb2.Duration
    synthetic_traffic: bool
    synthetic_traffic_warmup: _duration_pb2.Duration
    prober_binary: str
    prober_image: str
    prober_url: str
    clean_responder_helpers: bool
    state_baseline: bool
    def __init__(self, namespace: _Optional[str] = ..., control_namespace: _Optional[str] = ..., app_root: _Optional[str] = ..., application: _Optional[str] = ..., source_commit: _Optional[str] = ..., deployed_commit: _Optional[str] = ..., dispatcher: _Optional[str] = ..., dispatcher_mode: _Optional[str] = ..., dispatcher_args: _Optional[_Iterable[str]] = ..., responder_image: _Optional[str] = ..., repository_pvc: _Optional[str] = ..., repository_mount_path: _Optional[str] = ..., repository_pvc_subpath: _Optional[str] = ..., responder_credentials_secret: _Optional[str] = ..., responder_env: _Optional[_Iterable[str]] = ..., broker: _Optional[str] = ..., broker_worktree_root: _Optional[str] = ..., broker_args: _Optional[_Iterable[str]] = ..., firing_telemetry_path: _Optional[str] = ..., duration: _Optional[_Union[datetime.timedelta, _duration_pb2.Duration, _Mapping]] = ..., response_timeout: _Optional[_Union[datetime.timedelta, _duration_pb2.Duration, _Mapping]] = ..., verification_timeout: _Optional[_Union[datetime.timedelta, _duration_pb2.Duration, _Mapping]] = ..., max_follow_ups: _Optional[int] = ..., follow_up_cooldown: _Optional[_Union[datetime.timedelta, _duration_pb2.Duration, _Mapping]] = ..., repair_policy: _Optional[str] = ..., lease_name: _Optional[str] = ..., lease_duration: _Optional[_Union[datetime.timedelta, _duration_pb2.Duration, _Mapping]] = ..., identity: _Optional[str] = ..., exit_after_closure: _Optional[bool] = ..., restart_after_closure: _Optional[bool] = ..., maintenance_configmap: _Optional[str] = ..., maintenance_poll_interval: _Optional[_Union[datetime.timedelta, _duration_pb2.Duration, _Mapping]] = ..., resume_sync_timeout: _Optional[_Union[datetime.timedelta, _duration_pb2.Duration, _Mapping]] = ..., synthetic_traffic: _Optional[bool] = ..., synthetic_traffic_warmup: _Optional[_Union[datetime.timedelta, _duration_pb2.Duration, _Mapping]] = ..., prober_binary: _Optional[str] = ..., prober_image: _Optional[str] = ..., prober_url: _Optional[str] = ..., clean_responder_helpers: _Optional[bool] = ..., state_baseline: _Optional[bool] = ...) -> None: ...
