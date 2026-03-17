from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class RefreshMinecraftSyncRequest(_message.Message):
    __slots__ = ("server_id", "role_id", "import_snapshot", "reconcile")
    SERVER_ID_FIELD_NUMBER: _ClassVar[int]
    ROLE_ID_FIELD_NUMBER: _ClassVar[int]
    IMPORT_SNAPSHOT_FIELD_NUMBER: _ClassVar[int]
    RECONCILE_FIELD_NUMBER: _ClassVar[int]
    server_id: int
    role_id: int
    import_snapshot: bool
    reconcile: bool
    def __init__(self, server_id: _Optional[int] = ..., role_id: _Optional[int] = ..., import_snapshot: bool = ..., reconcile: bool = ...) -> None: ...

class MinecraftSyncResponse(_message.Message):
    __slots__ = ("roles", "imported", "reconciled", "supported", "message")
    ROLES_FIELD_NUMBER: _ClassVar[int]
    IMPORTED_FIELD_NUMBER: _ClassVar[int]
    RECONCILED_FIELD_NUMBER: _ClassVar[int]
    SUPPORTED_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    roles: int
    imported: int
    reconciled: int
    supported: bool
    message: str
    def __init__(self, roles: _Optional[int] = ..., imported: _Optional[int] = ..., reconciled: _Optional[int] = ..., supported: bool = ..., message: _Optional[str] = ...) -> None: ...
