from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class RefreshDiscordSyncRequest(_message.Message):
    __slots__ = ("project_id", "role_id", "import_snapshot", "reconcile")
    PROJECT_ID_FIELD_NUMBER: _ClassVar[int]
    ROLE_ID_FIELD_NUMBER: _ClassVar[int]
    IMPORT_SNAPSHOT_FIELD_NUMBER: _ClassVar[int]
    RECONCILE_FIELD_NUMBER: _ClassVar[int]
    project_id: int
    role_id: int
    import_snapshot: bool
    reconcile: bool
    def __init__(self, project_id: _Optional[int] = ..., role_id: _Optional[int] = ..., import_snapshot: bool = ..., reconcile: bool = ...) -> None: ...

class DiscordSyncResponse(_message.Message):
    __slots__ = ("roles", "imported", "reconciled")
    ROLES_FIELD_NUMBER: _ClassVar[int]
    IMPORTED_FIELD_NUMBER: _ClassVar[int]
    RECONCILED_FIELD_NUMBER: _ClassVar[int]
    roles: int
    imported: int
    reconciled: int
    def __init__(self, roles: _Optional[int] = ..., imported: _Optional[int] = ..., reconciled: _Optional[int] = ...) -> None: ...
