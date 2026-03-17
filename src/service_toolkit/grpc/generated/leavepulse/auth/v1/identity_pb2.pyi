from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class ResolveDiscordRequest(_message.Message):
    __slots__ = ("discord_subject",)
    DISCORD_SUBJECT_FIELD_NUMBER: _ClassVar[int]
    discord_subject: str
    def __init__(self, discord_subject: _Optional[str] = ...) -> None: ...

class EnsureUserRequest(_message.Message):
    __slots__ = ("discord_subject",)
    DISCORD_SUBJECT_FIELD_NUMBER: _ClassVar[int]
    discord_subject: str
    def __init__(self, discord_subject: _Optional[str] = ...) -> None: ...

class IdentityLookupResponse(_message.Message):
    __slots__ = ("found", "user_id")
    FOUND_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    found: bool
    user_id: int
    def __init__(self, found: bool = ..., user_id: _Optional[int] = ...) -> None: ...

class EnsureUserResponse(_message.Message):
    __slots__ = ("found", "user_id", "created", "is_shadow")
    FOUND_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    CREATED_FIELD_NUMBER: _ClassVar[int]
    IS_SHADOW_FIELD_NUMBER: _ClassVar[int]
    found: bool
    user_id: int
    created: bool
    is_shadow: bool
    def __init__(self, found: bool = ..., user_id: _Optional[int] = ..., created: bool = ..., is_shadow: bool = ...) -> None: ...

class ResolveMinecraftRequest(_message.Message):
    __slots__ = ("minecraft_uuid", "project_id")
    MINECRAFT_UUID_FIELD_NUMBER: _ClassVar[int]
    PROJECT_ID_FIELD_NUMBER: _ClassVar[int]
    minecraft_uuid: str
    project_id: int
    def __init__(self, minecraft_uuid: _Optional[str] = ..., project_id: _Optional[int] = ...) -> None: ...

class ResolveMinecraftByNickRequest(_message.Message):
    __slots__ = ("minecraft_nick", "project_id")
    MINECRAFT_NICK_FIELD_NUMBER: _ClassVar[int]
    PROJECT_ID_FIELD_NUMBER: _ClassVar[int]
    minecraft_nick: str
    project_id: int
    def __init__(self, minecraft_nick: _Optional[str] = ..., project_id: _Optional[int] = ...) -> None: ...
