from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class CompleteLinkCodeRequest(_message.Message):
    __slots__ = ("code", "minecraft_nick", "minecraft_uuid", "server_id", "project_id")
    CODE_FIELD_NUMBER: _ClassVar[int]
    MINECRAFT_NICK_FIELD_NUMBER: _ClassVar[int]
    MINECRAFT_UUID_FIELD_NUMBER: _ClassVar[int]
    SERVER_ID_FIELD_NUMBER: _ClassVar[int]
    PROJECT_ID_FIELD_NUMBER: _ClassVar[int]
    code: str
    minecraft_nick: str
    minecraft_uuid: str
    server_id: int
    project_id: int
    def __init__(self, code: _Optional[str] = ..., minecraft_nick: _Optional[str] = ..., minecraft_uuid: _Optional[str] = ..., server_id: _Optional[int] = ..., project_id: _Optional[int] = ...) -> None: ...

class LinkedMinecraftAccount(_message.Message):
    __slots__ = ("id", "account_type", "link_source", "verification_status", "identity_scope_type", "identity_scope_id", "minecraft_uuid", "minecraft_nick", "proof_server_id")
    ID_FIELD_NUMBER: _ClassVar[int]
    ACCOUNT_TYPE_FIELD_NUMBER: _ClassVar[int]
    LINK_SOURCE_FIELD_NUMBER: _ClassVar[int]
    VERIFICATION_STATUS_FIELD_NUMBER: _ClassVar[int]
    IDENTITY_SCOPE_TYPE_FIELD_NUMBER: _ClassVar[int]
    IDENTITY_SCOPE_ID_FIELD_NUMBER: _ClassVar[int]
    MINECRAFT_UUID_FIELD_NUMBER: _ClassVar[int]
    MINECRAFT_NICK_FIELD_NUMBER: _ClassVar[int]
    PROOF_SERVER_ID_FIELD_NUMBER: _ClassVar[int]
    id: int
    account_type: str
    link_source: str
    verification_status: str
    identity_scope_type: str
    identity_scope_id: int
    minecraft_uuid: str
    minecraft_nick: str
    proof_server_id: int
    def __init__(self, id: _Optional[int] = ..., account_type: _Optional[str] = ..., link_source: _Optional[str] = ..., verification_status: _Optional[str] = ..., identity_scope_type: _Optional[str] = ..., identity_scope_id: _Optional[int] = ..., minecraft_uuid: _Optional[str] = ..., minecraft_nick: _Optional[str] = ..., proof_server_id: _Optional[int] = ...) -> None: ...

class CompleteLinkCodeResponse(_message.Message):
    __slots__ = ("status", "user_id", "account")
    STATUS_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    ACCOUNT_FIELD_NUMBER: _ClassVar[int]
    status: str
    user_id: int
    account: LinkedMinecraftAccount
    def __init__(self, status: _Optional[str] = ..., user_id: _Optional[int] = ..., account: _Optional[_Union[LinkedMinecraftAccount, _Mapping]] = ...) -> None: ...
