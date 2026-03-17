from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class GetHistoryRequest(_message.Message):
    __slots__ = ("resource_id", "period")
    RESOURCE_ID_FIELD_NUMBER: _ClassVar[int]
    PERIOD_FIELD_NUMBER: _ClassVar[int]
    resource_id: int
    period: str
    def __init__(self, resource_id: _Optional[int] = ..., period: _Optional[str] = ...) -> None: ...

class HistoryPoint(_message.Message):
    __slots__ = ("collected_at", "avg_online", "peak_online", "last_online", "max", "status", "status_source", "exclude_from_score")
    COLLECTED_AT_FIELD_NUMBER: _ClassVar[int]
    AVG_ONLINE_FIELD_NUMBER: _ClassVar[int]
    PEAK_ONLINE_FIELD_NUMBER: _ClassVar[int]
    LAST_ONLINE_FIELD_NUMBER: _ClassVar[int]
    MAX_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    STATUS_SOURCE_FIELD_NUMBER: _ClassVar[int]
    EXCLUDE_FROM_SCORE_FIELD_NUMBER: _ClassVar[int]
    collected_at: str
    avg_online: float
    peak_online: int
    last_online: int
    max: int
    status: str
    status_source: str
    exclude_from_score: bool
    def __init__(self, collected_at: _Optional[str] = ..., avg_online: _Optional[float] = ..., peak_online: _Optional[int] = ..., last_online: _Optional[int] = ..., max: _Optional[int] = ..., status: _Optional[str] = ..., status_source: _Optional[str] = ..., exclude_from_score: bool = ...) -> None: ...

class HistoryResponse(_message.Message):
    __slots__ = ("period", "points")
    PERIOD_FIELD_NUMBER: _ClassVar[int]
    POINTS_FIELD_NUMBER: _ClassVar[int]
    period: str
    points: _containers.RepeatedCompositeFieldContainer[HistoryPoint]
    def __init__(self, period: _Optional[str] = ..., points: _Optional[_Iterable[_Union[HistoryPoint, _Mapping]]] = ...) -> None: ...
