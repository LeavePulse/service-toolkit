from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class CheckPermissionRequest(_message.Message):
    __slots__ = ("resource_id", "user_id", "permission_code", "scope_type", "scope_id")
    RESOURCE_ID_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    PERMISSION_CODE_FIELD_NUMBER: _ClassVar[int]
    SCOPE_TYPE_FIELD_NUMBER: _ClassVar[int]
    SCOPE_ID_FIELD_NUMBER: _ClassVar[int]
    resource_id: int
    user_id: int
    permission_code: str
    scope_type: str
    scope_id: int
    def __init__(self, resource_id: _Optional[int] = ..., user_id: _Optional[int] = ..., permission_code: _Optional[str] = ..., scope_type: _Optional[str] = ..., scope_id: _Optional[int] = ...) -> None: ...

class PermissionCheckResponse(_message.Message):
    __slots__ = ("project_id", "user_id", "permission_code", "is_owner", "member_state", "has_permission", "scope_type", "scope_id")
    PROJECT_ID_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    PERMISSION_CODE_FIELD_NUMBER: _ClassVar[int]
    IS_OWNER_FIELD_NUMBER: _ClassVar[int]
    MEMBER_STATE_FIELD_NUMBER: _ClassVar[int]
    HAS_PERMISSION_FIELD_NUMBER: _ClassVar[int]
    SCOPE_TYPE_FIELD_NUMBER: _ClassVar[int]
    SCOPE_ID_FIELD_NUMBER: _ClassVar[int]
    project_id: int
    user_id: int
    permission_code: str
    is_owner: bool
    member_state: str
    has_permission: bool
    scope_type: str
    scope_id: int
    def __init__(self, project_id: _Optional[int] = ..., user_id: _Optional[int] = ..., permission_code: _Optional[str] = ..., is_owner: bool = ..., member_state: _Optional[str] = ..., has_permission: bool = ..., scope_type: _Optional[str] = ..., scope_id: _Optional[int] = ...) -> None: ...
