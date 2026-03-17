from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class TeamSyncSubject(_message.Message):
    __slots__ = ("display_name", "avatar_url", "user_id", "discord_user_id", "minecraft_uuid", "is_public")
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    AVATAR_URL_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    DISCORD_USER_ID_FIELD_NUMBER: _ClassVar[int]
    MINECRAFT_UUID_FIELD_NUMBER: _ClassVar[int]
    IS_PUBLIC_FIELD_NUMBER: _ClassVar[int]
    display_name: str
    avatar_url: str
    user_id: int
    discord_user_id: str
    minecraft_uuid: str
    is_public: bool
    def __init__(self, display_name: _Optional[str] = ..., avatar_url: _Optional[str] = ..., user_id: _Optional[int] = ..., discord_user_id: _Optional[str] = ..., minecraft_uuid: _Optional[str] = ..., is_public: bool = ...) -> None: ...

class GetDiscordRoleTargetsRequest(_message.Message):
    __slots__ = ("project_id", "role_id")
    PROJECT_ID_FIELD_NUMBER: _ClassVar[int]
    ROLE_ID_FIELD_NUMBER: _ClassVar[int]
    project_id: int
    role_id: int
    def __init__(self, project_id: _Optional[int] = ..., role_id: _Optional[int] = ...) -> None: ...

class DiscordRoleTargetItem(_message.Message):
    __slots__ = ("role_id", "role_key", "role_name", "discord_role_id", "desired_discord_user_ids")
    ROLE_ID_FIELD_NUMBER: _ClassVar[int]
    ROLE_KEY_FIELD_NUMBER: _ClassVar[int]
    ROLE_NAME_FIELD_NUMBER: _ClassVar[int]
    DISCORD_ROLE_ID_FIELD_NUMBER: _ClassVar[int]
    DESIRED_DISCORD_USER_IDS_FIELD_NUMBER: _ClassVar[int]
    role_id: int
    role_key: str
    role_name: str
    discord_role_id: str
    desired_discord_user_ids: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, role_id: _Optional[int] = ..., role_key: _Optional[str] = ..., role_name: _Optional[str] = ..., discord_role_id: _Optional[str] = ..., desired_discord_user_ids: _Optional[_Iterable[str]] = ...) -> None: ...

class DiscordRoleTargetsResponse(_message.Message):
    __slots__ = ("project_id", "items")
    PROJECT_ID_FIELD_NUMBER: _ClassVar[int]
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    project_id: int
    items: _containers.RepeatedCompositeFieldContainer[DiscordRoleTargetItem]
    def __init__(self, project_id: _Optional[int] = ..., items: _Optional[_Iterable[_Union[DiscordRoleTargetItem, _Mapping]]] = ...) -> None: ...

class GetMinecraftGroupTargetsRequest(_message.Message):
    __slots__ = ("server_id", "role_id")
    SERVER_ID_FIELD_NUMBER: _ClassVar[int]
    ROLE_ID_FIELD_NUMBER: _ClassVar[int]
    server_id: int
    role_id: int
    def __init__(self, server_id: _Optional[int] = ..., role_id: _Optional[int] = ...) -> None: ...

class MinecraftGroupTargetItem(_message.Message):
    __slots__ = ("server_id", "role_id", "role_key", "role_name", "luckperms_group", "desired_minecraft_uuids")
    SERVER_ID_FIELD_NUMBER: _ClassVar[int]
    ROLE_ID_FIELD_NUMBER: _ClassVar[int]
    ROLE_KEY_FIELD_NUMBER: _ClassVar[int]
    ROLE_NAME_FIELD_NUMBER: _ClassVar[int]
    LUCKPERMS_GROUP_FIELD_NUMBER: _ClassVar[int]
    DESIRED_MINECRAFT_UUIDS_FIELD_NUMBER: _ClassVar[int]
    server_id: int
    role_id: int
    role_key: str
    role_name: str
    luckperms_group: str
    desired_minecraft_uuids: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, server_id: _Optional[int] = ..., role_id: _Optional[int] = ..., role_key: _Optional[str] = ..., role_name: _Optional[str] = ..., luckperms_group: _Optional[str] = ..., desired_minecraft_uuids: _Optional[_Iterable[str]] = ...) -> None: ...

class MinecraftGroupTargetsResponse(_message.Message):
    __slots__ = ("project_id", "server_id", "items")
    PROJECT_ID_FIELD_NUMBER: _ClassVar[int]
    SERVER_ID_FIELD_NUMBER: _ClassVar[int]
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    project_id: int
    server_id: int
    items: _containers.RepeatedCompositeFieldContainer[MinecraftGroupTargetItem]
    def __init__(self, project_id: _Optional[int] = ..., server_id: _Optional[int] = ..., items: _Optional[_Iterable[_Union[MinecraftGroupTargetItem, _Mapping]]] = ...) -> None: ...

class SourceGrantsSnapshotRequest(_message.Message):
    __slots__ = ("project_id", "role_id", "source_type", "source_ref", "scope_type", "scope_id", "subjects")
    PROJECT_ID_FIELD_NUMBER: _ClassVar[int]
    ROLE_ID_FIELD_NUMBER: _ClassVar[int]
    SOURCE_TYPE_FIELD_NUMBER: _ClassVar[int]
    SOURCE_REF_FIELD_NUMBER: _ClassVar[int]
    SCOPE_TYPE_FIELD_NUMBER: _ClassVar[int]
    SCOPE_ID_FIELD_NUMBER: _ClassVar[int]
    SUBJECTS_FIELD_NUMBER: _ClassVar[int]
    project_id: int
    role_id: int
    source_type: str
    source_ref: str
    scope_type: str
    scope_id: int
    subjects: _containers.RepeatedCompositeFieldContainer[TeamSyncSubject]
    def __init__(self, project_id: _Optional[int] = ..., role_id: _Optional[int] = ..., source_type: _Optional[str] = ..., source_ref: _Optional[str] = ..., scope_type: _Optional[str] = ..., scope_id: _Optional[int] = ..., subjects: _Optional[_Iterable[_Union[TeamSyncSubject, _Mapping]]] = ...) -> None: ...

class SourceGrantsMemberRequest(_message.Message):
    __slots__ = ("project_id", "role_id", "source_type", "source_ref", "active", "scope_type", "scope_id", "subject")
    PROJECT_ID_FIELD_NUMBER: _ClassVar[int]
    ROLE_ID_FIELD_NUMBER: _ClassVar[int]
    SOURCE_TYPE_FIELD_NUMBER: _ClassVar[int]
    SOURCE_REF_FIELD_NUMBER: _ClassVar[int]
    ACTIVE_FIELD_NUMBER: _ClassVar[int]
    SCOPE_TYPE_FIELD_NUMBER: _ClassVar[int]
    SCOPE_ID_FIELD_NUMBER: _ClassVar[int]
    SUBJECT_FIELD_NUMBER: _ClassVar[int]
    project_id: int
    role_id: int
    source_type: str
    source_ref: str
    active: bool
    scope_type: str
    scope_id: int
    subject: TeamSyncSubject
    def __init__(self, project_id: _Optional[int] = ..., role_id: _Optional[int] = ..., source_type: _Optional[str] = ..., source_ref: _Optional[str] = ..., active: bool = ..., scope_type: _Optional[str] = ..., scope_id: _Optional[int] = ..., subject: _Optional[_Union[TeamSyncSubject, _Mapping]] = ...) -> None: ...

class TeamSyncMutationResponse(_message.Message):
    __slots__ = ("project_id", "role_id", "affected_members", "materialized")
    PROJECT_ID_FIELD_NUMBER: _ClassVar[int]
    ROLE_ID_FIELD_NUMBER: _ClassVar[int]
    AFFECTED_MEMBERS_FIELD_NUMBER: _ClassVar[int]
    MATERIALIZED_FIELD_NUMBER: _ClassVar[int]
    project_id: int
    role_id: int
    affected_members: int
    materialized: bool
    def __init__(self, project_id: _Optional[int] = ..., role_id: _Optional[int] = ..., affected_members: _Optional[int] = ..., materialized: bool = ...) -> None: ...
