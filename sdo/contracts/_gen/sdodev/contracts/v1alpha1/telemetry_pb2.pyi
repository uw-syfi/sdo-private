import datetime

from buf.validate import validate_pb2 as _validate_pb2
from google.protobuf import timestamp_pb2 as _timestamp_pb2
from sdodev.contracts.v1alpha1 import common_pb2 as _common_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class FiringRecord(_message.Message):
    __slots__ = ("schema_version", "event_id", "event", "recorded_at", "evaluation_iteration", "application", "namespace", "detector_id", "detector_class", "owner", "rule_id", "fingerprint", "severity", "parameter_bindings", "surfaced_playbooks", "firing_count", "firing_threshold", "clear_count", "clear_threshold", "incident_id", "dispatch_relation")
    class ParameterBindingsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _common_pb2.ObjectRef
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_common_pb2.ObjectRef, _Mapping]] = ...) -> None: ...
    SCHEMA_VERSION_FIELD_NUMBER: _ClassVar[int]
    EVENT_ID_FIELD_NUMBER: _ClassVar[int]
    EVENT_FIELD_NUMBER: _ClassVar[int]
    RECORDED_AT_FIELD_NUMBER: _ClassVar[int]
    EVALUATION_ITERATION_FIELD_NUMBER: _ClassVar[int]
    APPLICATION_FIELD_NUMBER: _ClassVar[int]
    NAMESPACE_FIELD_NUMBER: _ClassVar[int]
    DETECTOR_ID_FIELD_NUMBER: _ClassVar[int]
    DETECTOR_CLASS_FIELD_NUMBER: _ClassVar[int]
    OWNER_FIELD_NUMBER: _ClassVar[int]
    RULE_ID_FIELD_NUMBER: _ClassVar[int]
    FINGERPRINT_FIELD_NUMBER: _ClassVar[int]
    SEVERITY_FIELD_NUMBER: _ClassVar[int]
    PARAMETER_BINDINGS_FIELD_NUMBER: _ClassVar[int]
    SURFACED_PLAYBOOKS_FIELD_NUMBER: _ClassVar[int]
    FIRING_COUNT_FIELD_NUMBER: _ClassVar[int]
    FIRING_THRESHOLD_FIELD_NUMBER: _ClassVar[int]
    CLEAR_COUNT_FIELD_NUMBER: _ClassVar[int]
    CLEAR_THRESHOLD_FIELD_NUMBER: _ClassVar[int]
    INCIDENT_ID_FIELD_NUMBER: _ClassVar[int]
    DISPATCH_RELATION_FIELD_NUMBER: _ClassVar[int]
    schema_version: str
    event_id: str
    event: str
    recorded_at: _timestamp_pb2.Timestamp
    evaluation_iteration: int
    application: str
    namespace: str
    detector_id: str
    detector_class: str
    owner: str
    rule_id: str
    fingerprint: str
    severity: str
    parameter_bindings: _containers.MessageMap[str, _common_pb2.ObjectRef]
    surfaced_playbooks: _containers.RepeatedScalarFieldContainer[str]
    firing_count: int
    firing_threshold: int
    clear_count: int
    clear_threshold: int
    incident_id: str
    dispatch_relation: str
    def __init__(self, schema_version: _Optional[str] = ..., event_id: _Optional[str] = ..., event: _Optional[str] = ..., recorded_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., evaluation_iteration: _Optional[int] = ..., application: _Optional[str] = ..., namespace: _Optional[str] = ..., detector_id: _Optional[str] = ..., detector_class: _Optional[str] = ..., owner: _Optional[str] = ..., rule_id: _Optional[str] = ..., fingerprint: _Optional[str] = ..., severity: _Optional[str] = ..., parameter_bindings: _Optional[_Mapping[str, _common_pb2.ObjectRef]] = ..., surfaced_playbooks: _Optional[_Iterable[str]] = ..., firing_count: _Optional[int] = ..., firing_threshold: _Optional[int] = ..., clear_count: _Optional[int] = ..., clear_threshold: _Optional[int] = ..., incident_id: _Optional[str] = ..., dispatch_relation: _Optional[str] = ...) -> None: ...
