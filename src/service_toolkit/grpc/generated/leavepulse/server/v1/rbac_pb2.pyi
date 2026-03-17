from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class PermsSnapshotRequest(_message.Message):
    __slots__ = ("resource_id",)
    RESOURCE_ID_FIELD_NUMBER: _ClassVar[int]
    resource_id: int
    def __init__(self, resource_id: _Optional[int] = ...) -> None: ...

class PermsSnapshotResponse(_message.Message):
    __slots__ = ("project_id", "owner_user_id", "owner_synced", "published")
    PROJECT_ID_FIELD_NUMBER: _ClassVar[int]
    OWNER_USER_ID_FIELD_NUMBER: _ClassVar[int]
    OWNER_SYNCED_FIELD_NUMBER: _ClassVar[int]
    PUBLISHED_FIELD_NUMBER: _ClassVar[int]
    project_id: int
    owner_user_id: int
    owner_synced: bool
    published: bool
    def __init__(self, project_id: _Optional[int] = ..., owner_user_id: _Optional[int] = ..., owner_synced: bool = ..., published: bool = ...) -> None: ...
