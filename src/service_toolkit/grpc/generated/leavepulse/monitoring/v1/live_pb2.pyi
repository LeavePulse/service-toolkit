from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class GetLiveRequest(_message.Message):
    __slots__ = ("server_id",)
    SERVER_ID_FIELD_NUMBER: _ClassVar[int]
    server_id: int
    def __init__(self, server_id: _Optional[int] = ...) -> None: ...

class BatchLiveRequest(_message.Message):
    __slots__ = ("server_ids",)
    SERVER_IDS_FIELD_NUMBER: _ClassVar[int]
    server_ids: _containers.RepeatedScalarFieldContainer[int]
    def __init__(self, server_ids: _Optional[_Iterable[int]] = ...) -> None: ...

class ServerLiveStatus(_message.Message):
    __slots__ = ("server_id", "collected_at", "online", "max", "version", "motd", "country", "country_code", "source", "connection_state", "online_state", "freshness_state", "online_reason", "players")
    SERVER_ID_FIELD_NUMBER: _ClassVar[int]
    COLLECTED_AT_FIELD_NUMBER: _ClassVar[int]
    ONLINE_FIELD_NUMBER: _ClassVar[int]
    MAX_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    MOTD_FIELD_NUMBER: _ClassVar[int]
    COUNTRY_FIELD_NUMBER: _ClassVar[int]
    COUNTRY_CODE_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    CONNECTION_STATE_FIELD_NUMBER: _ClassVar[int]
    ONLINE_STATE_FIELD_NUMBER: _ClassVar[int]
    FRESHNESS_STATE_FIELD_NUMBER: _ClassVar[int]
    ONLINE_REASON_FIELD_NUMBER: _ClassVar[int]
    PLAYERS_FIELD_NUMBER: _ClassVar[int]
    server_id: int
    collected_at: str
    online: int
    max: int
    version: str
    motd: str
    country: str
    country_code: str
    source: str
    connection_state: str
    online_state: str
    freshness_state: str
    online_reason: str
    players: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, server_id: _Optional[int] = ..., collected_at: _Optional[str] = ..., online: _Optional[int] = ..., max: _Optional[int] = ..., version: _Optional[str] = ..., motd: _Optional[str] = ..., country: _Optional[str] = ..., country_code: _Optional[str] = ..., source: _Optional[str] = ..., connection_state: _Optional[str] = ..., online_state: _Optional[str] = ..., freshness_state: _Optional[str] = ..., online_reason: _Optional[str] = ..., players: _Optional[_Iterable[str]] = ...) -> None: ...

class BatchLiveResponse(_message.Message):
    __slots__ = ("items",)
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    items: _containers.RepeatedCompositeFieldContainer[ServerLiveStatus]
    def __init__(self, items: _Optional[_Iterable[_Union[ServerLiveStatus, _Mapping]]] = ...) -> None: ...
