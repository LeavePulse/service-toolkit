from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ServerCatalogItem(_message.Message):
    __slots__ = ("id", "project_id", "project_online_strategy", "ip_or_domain", "server_role", "proxy_type", "parent_id", "ping_ip_or_domain", "ping_port", "bedrock_port", "game_edition", "is_verified", "verification_source")
    ID_FIELD_NUMBER: _ClassVar[int]
    PROJECT_ID_FIELD_NUMBER: _ClassVar[int]
    PROJECT_ONLINE_STRATEGY_FIELD_NUMBER: _ClassVar[int]
    IP_OR_DOMAIN_FIELD_NUMBER: _ClassVar[int]
    SERVER_ROLE_FIELD_NUMBER: _ClassVar[int]
    PROXY_TYPE_FIELD_NUMBER: _ClassVar[int]
    PARENT_ID_FIELD_NUMBER: _ClassVar[int]
    PING_IP_OR_DOMAIN_FIELD_NUMBER: _ClassVar[int]
    PING_PORT_FIELD_NUMBER: _ClassVar[int]
    BEDROCK_PORT_FIELD_NUMBER: _ClassVar[int]
    GAME_EDITION_FIELD_NUMBER: _ClassVar[int]
    IS_VERIFIED_FIELD_NUMBER: _ClassVar[int]
    VERIFICATION_SOURCE_FIELD_NUMBER: _ClassVar[int]
    id: str
    project_id: str
    project_online_strategy: str
    ip_or_domain: str
    server_role: str
    proxy_type: str
    parent_id: str
    ping_ip_or_domain: str
    ping_port: int
    bedrock_port: int
    game_edition: str
    is_verified: bool
    verification_source: str
    def __init__(self, id: _Optional[str] = ..., project_id: _Optional[str] = ..., project_online_strategy: _Optional[str] = ..., ip_or_domain: _Optional[str] = ..., server_role: _Optional[str] = ..., proxy_type: _Optional[str] = ..., parent_id: _Optional[str] = ..., ping_ip_or_domain: _Optional[str] = ..., ping_port: _Optional[int] = ..., bedrock_port: _Optional[int] = ..., game_edition: _Optional[str] = ..., is_verified: bool = ..., verification_source: _Optional[str] = ...) -> None: ...

class CatalogResolveRequest(_message.Message):
    __slots__ = ("server_ids",)
    SERVER_IDS_FIELD_NUMBER: _ClassVar[int]
    server_ids: _containers.RepeatedScalarFieldContainer[int]
    def __init__(self, server_ids: _Optional[_Iterable[int]] = ...) -> None: ...

class GetServerRequest(_message.Message):
    __slots__ = ("server_id",)
    SERVER_ID_FIELD_NUMBER: _ClassVar[int]
    server_id: int
    def __init__(self, server_id: _Optional[int] = ...) -> None: ...

class ListServersRequest(_message.Message):
    __slots__ = ("page", "limit", "role", "parent_id")
    PAGE_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    ROLE_FIELD_NUMBER: _ClassVar[int]
    PARENT_ID_FIELD_NUMBER: _ClassVar[int]
    page: int
    limit: int
    role: str
    parent_id: int
    def __init__(self, page: _Optional[int] = ..., limit: _Optional[int] = ..., role: _Optional[str] = ..., parent_id: _Optional[int] = ...) -> None: ...

class ServerCatalogListResponse(_message.Message):
    __slots__ = ("items", "total", "page", "limit")
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    TOTAL_FIELD_NUMBER: _ClassVar[int]
    PAGE_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    items: _containers.RepeatedCompositeFieldContainer[ServerCatalogItem]
    total: int
    page: int
    limit: int
    def __init__(self, items: _Optional[_Iterable[_Union[ServerCatalogItem, _Mapping]]] = ..., total: _Optional[int] = ..., page: _Optional[int] = ..., limit: _Optional[int] = ...) -> None: ...

class GetRootRequest(_message.Message):
    __slots__ = ("server_id",)
    SERVER_ID_FIELD_NUMBER: _ClassVar[int]
    server_id: int
    def __init__(self, server_id: _Optional[int] = ...) -> None: ...

class GetRootResponse(_message.Message):
    __slots__ = ("server_id", "project_id", "root_server_id")
    SERVER_ID_FIELD_NUMBER: _ClassVar[int]
    PROJECT_ID_FIELD_NUMBER: _ClassVar[int]
    ROOT_SERVER_ID_FIELD_NUMBER: _ClassVar[int]
    server_id: int
    project_id: int
    root_server_id: int
    def __init__(self, server_id: _Optional[int] = ..., project_id: _Optional[int] = ..., root_server_id: _Optional[int] = ...) -> None: ...

class GetSubserversRequest(_message.Message):
    __slots__ = ("server_id",)
    SERVER_ID_FIELD_NUMBER: _ClassVar[int]
    server_id: int
    def __init__(self, server_id: _Optional[int] = ...) -> None: ...

class GetSubserversResponse(_message.Message):
    __slots__ = ("subserver_ids",)
    SUBSERVER_IDS_FIELD_NUMBER: _ClassVar[int]
    subserver_ids: _containers.RepeatedScalarFieldContainer[int]
    def __init__(self, subserver_ids: _Optional[_Iterable[int]] = ...) -> None: ...
