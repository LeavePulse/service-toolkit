from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class GetProfileRequest(_message.Message):
    __slots__ = ("user_id",)
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    user_id: int
    def __init__(self, user_id: _Optional[int] = ...) -> None: ...

class ProfileResponse(_message.Message):
    __slots__ = ("found", "username", "avatar_url")
    FOUND_FIELD_NUMBER: _ClassVar[int]
    USERNAME_FIELD_NUMBER: _ClassVar[int]
    AVATAR_URL_FIELD_NUMBER: _ClassVar[int]
    found: bool
    username: str
    avatar_url: str
    def __init__(self, found: bool = ..., username: _Optional[str] = ..., avatar_url: _Optional[str] = ...) -> None: ...

class GetMinecraftAccountsRequest(_message.Message):
    __slots__ = ("user_id",)
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    user_id: int
    def __init__(self, user_id: _Optional[int] = ...) -> None: ...

class MinecraftAccount(_message.Message):
    __slots__ = ("account_type", "minecraft_uuid", "minecraft_nick")
    ACCOUNT_TYPE_FIELD_NUMBER: _ClassVar[int]
    MINECRAFT_UUID_FIELD_NUMBER: _ClassVar[int]
    MINECRAFT_NICK_FIELD_NUMBER: _ClassVar[int]
    account_type: str
    minecraft_uuid: str
    minecraft_nick: str
    def __init__(self, account_type: _Optional[str] = ..., minecraft_uuid: _Optional[str] = ..., minecraft_nick: _Optional[str] = ...) -> None: ...

class MinecraftAccountListResponse(_message.Message):
    __slots__ = ("items",)
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    items: _containers.RepeatedCompositeFieldContainer[MinecraftAccount]
    def __init__(self, items: _Optional[_Iterable[_Union[MinecraftAccount, _Mapping]]] = ...) -> None: ...

class GetDiscordSubjectRequest(_message.Message):
    __slots__ = ("user_id",)
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    user_id: int
    def __init__(self, user_id: _Optional[int] = ...) -> None: ...

class DiscordSubjectResponse(_message.Message):
    __slots__ = ("found", "discord_subject")
    FOUND_FIELD_NUMBER: _ClassVar[int]
    DISCORD_SUBJECT_FIELD_NUMBER: _ClassVar[int]
    found: bool
    discord_subject: str
    def __init__(self, found: bool = ..., discord_subject: _Optional[str] = ...) -> None: ...

class GetContactRequest(_message.Message):
    __slots__ = ("user_id",)
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    user_id: int
    def __init__(self, user_id: _Optional[int] = ...) -> None: ...

class ContactResponse(_message.Message):
    __slots__ = ("found", "email", "username")
    FOUND_FIELD_NUMBER: _ClassVar[int]
    EMAIL_FIELD_NUMBER: _ClassVar[int]
    USERNAME_FIELD_NUMBER: _ClassVar[int]
    found: bool
    email: str
    username: str
    def __init__(self, found: bool = ..., email: _Optional[str] = ..., username: _Optional[str] = ...) -> None: ...
