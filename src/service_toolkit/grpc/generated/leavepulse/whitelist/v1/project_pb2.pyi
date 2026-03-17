from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class ProofCompleteRequest(_message.Message):
    __slots__ = ("project_id", "user_id", "minecraft_account_type", "minecraft_uuid", "minecraft_nick")
    PROJECT_ID_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    MINECRAFT_ACCOUNT_TYPE_FIELD_NUMBER: _ClassVar[int]
    MINECRAFT_UUID_FIELD_NUMBER: _ClassVar[int]
    MINECRAFT_NICK_FIELD_NUMBER: _ClassVar[int]
    project_id: int
    user_id: int
    minecraft_account_type: str
    minecraft_uuid: str
    minecraft_nick: str
    def __init__(self, project_id: _Optional[int] = ..., user_id: _Optional[int] = ..., minecraft_account_type: _Optional[str] = ..., minecraft_uuid: _Optional[str] = ..., minecraft_nick: _Optional[str] = ...) -> None: ...

class ProofCompleteResponse(_message.Message):
    __slots__ = ("finalized_count", "finalized_application_ids")
    FINALIZED_COUNT_FIELD_NUMBER: _ClassVar[int]
    FINALIZED_APPLICATION_IDS_FIELD_NUMBER: _ClassVar[int]
    finalized_count: int
    finalized_application_ids: _containers.RepeatedScalarFieldContainer[int]
    def __init__(self, finalized_count: _Optional[int] = ..., finalized_application_ids: _Optional[_Iterable[int]] = ...) -> None: ...
