from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class GetMaintenanceRequest(_message.Message):
    __slots__ = ("server_id",)
    SERVER_ID_FIELD_NUMBER: _ClassVar[int]
    server_id: int
    def __init__(self, server_id: _Optional[int] = ...) -> None: ...

class MaintenanceState(_message.Message):
    __slots__ = ("enabled", "message")
    ENABLED_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    enabled: bool
    message: str
    def __init__(self, enabled: bool = ..., message: _Optional[str] = ...) -> None: ...

class UpdateEditionRequest(_message.Message):
    __slots__ = ("server_id", "game_edition")
    SERVER_ID_FIELD_NUMBER: _ClassVar[int]
    GAME_EDITION_FIELD_NUMBER: _ClassVar[int]
    server_id: int
    game_edition: str
    def __init__(self, server_id: _Optional[int] = ..., game_edition: _Optional[str] = ...) -> None: ...

class UpdateEditionResponse(_message.Message):
    __slots__ = ("item",)
    ITEM_FIELD_NUMBER: _ClassVar[int]
    item: ServerCatalogItemBrief
    def __init__(self, item: _Optional[_Union[ServerCatalogItemBrief, _Mapping]] = ...) -> None: ...

class ServerCatalogItemBrief(_message.Message):
    __slots__ = ("id", "game_edition")
    ID_FIELD_NUMBER: _ClassVar[int]
    GAME_EDITION_FIELD_NUMBER: _ClassVar[int]
    id: str
    game_edition: str
    def __init__(self, id: _Optional[str] = ..., game_edition: _Optional[str] = ...) -> None: ...

class StoreFaviconRequest(_message.Message):
    __slots__ = ("server_id", "data_url", "hash")
    SERVER_ID_FIELD_NUMBER: _ClassVar[int]
    DATA_URL_FIELD_NUMBER: _ClassVar[int]
    HASH_FIELD_NUMBER: _ClassVar[int]
    server_id: int
    data_url: str
    hash: str
    def __init__(self, server_id: _Optional[int] = ..., data_url: _Optional[str] = ..., hash: _Optional[str] = ...) -> None: ...

class StoreFaviconResponse(_message.Message):
    __slots__ = ("updated",)
    UPDATED_FIELD_NUMBER: _ClassVar[int]
    updated: bool
    def __init__(self, updated: bool = ...) -> None: ...
