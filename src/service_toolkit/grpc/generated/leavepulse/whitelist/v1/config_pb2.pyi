from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class GetWhitelistConfigRequest(_message.Message):
    __slots__ = ("server_id",)
    SERVER_ID_FIELD_NUMBER: _ClassVar[int]
    server_id: int
    def __init__(self, server_id: _Optional[int] = ...) -> None: ...

class WhitelistFormField(_message.Message):
    __slots__ = ("key", "label", "field_type", "required", "order")
    KEY_FIELD_NUMBER: _ClassVar[int]
    LABEL_FIELD_NUMBER: _ClassVar[int]
    FIELD_TYPE_FIELD_NUMBER: _ClassVar[int]
    REQUIRED_FIELD_NUMBER: _ClassVar[int]
    ORDER_FIELD_NUMBER: _ClassVar[int]
    key: str
    label: str
    field_type: str
    required: bool
    order: int
    def __init__(self, key: _Optional[str] = ..., label: _Optional[str] = ..., field_type: _Optional[str] = ..., required: bool = ..., order: _Optional[int] = ...) -> None: ...

class WhitelistEntry(_message.Message):
    __slots__ = ("user_id", "minecraft_uuid", "minecraft_nick", "minecraft_account_type", "discord_name")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    MINECRAFT_UUID_FIELD_NUMBER: _ClassVar[int]
    MINECRAFT_NICK_FIELD_NUMBER: _ClassVar[int]
    MINECRAFT_ACCOUNT_TYPE_FIELD_NUMBER: _ClassVar[int]
    DISCORD_NAME_FIELD_NUMBER: _ClassVar[int]
    user_id: int
    minecraft_uuid: str
    minecraft_nick: str
    minecraft_account_type: str
    discord_name: str
    def __init__(self, user_id: _Optional[int] = ..., minecraft_uuid: _Optional[str] = ..., minecraft_nick: _Optional[str] = ..., minecraft_account_type: _Optional[str] = ..., discord_name: _Optional[str] = ...) -> None: ...

class WhitelistConfigResponse(_message.Message):
    __slots__ = ("enabled", "binding_server_id", "scope_type", "enforcement_mode", "restrict_chat", "form_id", "form_name", "form_fields", "entries")
    ENABLED_FIELD_NUMBER: _ClassVar[int]
    BINDING_SERVER_ID_FIELD_NUMBER: _ClassVar[int]
    SCOPE_TYPE_FIELD_NUMBER: _ClassVar[int]
    ENFORCEMENT_MODE_FIELD_NUMBER: _ClassVar[int]
    RESTRICT_CHAT_FIELD_NUMBER: _ClassVar[int]
    FORM_ID_FIELD_NUMBER: _ClassVar[int]
    FORM_NAME_FIELD_NUMBER: _ClassVar[int]
    FORM_FIELDS_FIELD_NUMBER: _ClassVar[int]
    ENTRIES_FIELD_NUMBER: _ClassVar[int]
    enabled: bool
    binding_server_id: int
    scope_type: str
    enforcement_mode: str
    restrict_chat: bool
    form_id: int
    form_name: str
    form_fields: _containers.RepeatedCompositeFieldContainer[WhitelistFormField]
    entries: _containers.RepeatedCompositeFieldContainer[WhitelistEntry]
    def __init__(self, enabled: bool = ..., binding_server_id: _Optional[int] = ..., scope_type: _Optional[str] = ..., enforcement_mode: _Optional[str] = ..., restrict_chat: bool = ..., form_id: _Optional[int] = ..., form_name: _Optional[str] = ..., form_fields: _Optional[_Iterable[_Union[WhitelistFormField, _Mapping]]] = ..., entries: _Optional[_Iterable[_Union[WhitelistEntry, _Mapping]]] = ...) -> None: ...
