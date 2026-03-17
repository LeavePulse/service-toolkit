from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class IssueGatewayTokenRequest(_message.Message):
    __slots__ = ("server_id", "expires_in_hours")
    SERVER_ID_FIELD_NUMBER: _ClassVar[int]
    EXPIRES_IN_HOURS_FIELD_NUMBER: _ClassVar[int]
    server_id: int
    expires_in_hours: int
    def __init__(self, server_id: _Optional[int] = ..., expires_in_hours: _Optional[int] = ...) -> None: ...

class IssuedTokenResponse(_message.Message):
    __slots__ = ("token", "token_type", "expires_in", "audience", "roles", "scope", "tenant", "session_id")
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    TOKEN_TYPE_FIELD_NUMBER: _ClassVar[int]
    EXPIRES_IN_FIELD_NUMBER: _ClassVar[int]
    AUDIENCE_FIELD_NUMBER: _ClassVar[int]
    ROLES_FIELD_NUMBER: _ClassVar[int]
    SCOPE_FIELD_NUMBER: _ClassVar[int]
    TENANT_FIELD_NUMBER: _ClassVar[int]
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    token: str
    token_type: str
    expires_in: int
    audience: str
    roles: _containers.RepeatedScalarFieldContainer[str]
    scope: _containers.RepeatedScalarFieldContainer[str]
    tenant: str
    session_id: str
    def __init__(self, token: _Optional[str] = ..., token_type: _Optional[str] = ..., expires_in: _Optional[int] = ..., audience: _Optional[str] = ..., roles: _Optional[_Iterable[str]] = ..., scope: _Optional[_Iterable[str]] = ..., tenant: _Optional[str] = ..., session_id: _Optional[str] = ...) -> None: ...

class MinecraftAccountsStatsRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class MinecraftAccountsStatsResponse(_message.Message):
    __slots__ = ("registered_profiles",)
    REGISTERED_PROFILES_FIELD_NUMBER: _ClassVar[int]
    registered_profiles: int
    def __init__(self, registered_profiles: _Optional[int] = ...) -> None: ...
