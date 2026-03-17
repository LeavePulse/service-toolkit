from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ListApplicationsRequest(_message.Message):
    __slots__ = ("server_id", "status", "actor_user_id", "review_source")
    SERVER_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    ACTOR_USER_ID_FIELD_NUMBER: _ClassVar[int]
    REVIEW_SOURCE_FIELD_NUMBER: _ClassVar[int]
    server_id: int
    status: str
    actor_user_id: int
    review_source: str
    def __init__(self, server_id: _Optional[int] = ..., status: _Optional[str] = ..., actor_user_id: _Optional[int] = ..., review_source: _Optional[str] = ...) -> None: ...

class ListApplicationsResponse(_message.Message):
    __slots__ = ("applications",)
    APPLICATIONS_FIELD_NUMBER: _ClassVar[int]
    applications: _containers.RepeatedCompositeFieldContainer[Application]
    def __init__(self, applications: _Optional[_Iterable[_Union[Application, _Mapping]]] = ...) -> None: ...

class SetStatusRequest(_message.Message):
    __slots__ = ("server_id", "application_id", "status", "reason", "actor_user_id", "review_source")
    SERVER_ID_FIELD_NUMBER: _ClassVar[int]
    APPLICATION_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    ACTOR_USER_ID_FIELD_NUMBER: _ClassVar[int]
    REVIEW_SOURCE_FIELD_NUMBER: _ClassVar[int]
    server_id: int
    application_id: int
    status: str
    reason: str
    actor_user_id: int
    review_source: str
    def __init__(self, server_id: _Optional[int] = ..., application_id: _Optional[int] = ..., status: _Optional[str] = ..., reason: _Optional[str] = ..., actor_user_id: _Optional[int] = ..., review_source: _Optional[str] = ...) -> None: ...

class DecisionRequest(_message.Message):
    __slots__ = ("server_id", "application_id", "reason", "actor_user_id", "review_source")
    SERVER_ID_FIELD_NUMBER: _ClassVar[int]
    APPLICATION_ID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    ACTOR_USER_ID_FIELD_NUMBER: _ClassVar[int]
    REVIEW_SOURCE_FIELD_NUMBER: _ClassVar[int]
    server_id: int
    application_id: int
    reason: str
    actor_user_id: int
    review_source: str
    def __init__(self, server_id: _Optional[int] = ..., application_id: _Optional[int] = ..., reason: _Optional[str] = ..., actor_user_id: _Optional[int] = ..., review_source: _Optional[str] = ...) -> None: ...

class ApplicationResponse(_message.Message):
    __slots__ = ("application",)
    APPLICATION_FIELD_NUMBER: _ClassVar[int]
    application: Application
    def __init__(self, application: _Optional[_Union[Application, _Mapping]] = ...) -> None: ...

class Application(_message.Message):
    __slots__ = ("id", "user_id", "server_id", "form_id", "status", "status_alias", "created_at", "reviewed_at", "review_reason", "auto_approved", "minecraft_account_type", "minecraft_identity_state", "minecraft_nick", "minecraft_uuid", "discord_name", "application_url", "payload")
    class PayloadEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    ID_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    SERVER_ID_FIELD_NUMBER: _ClassVar[int]
    FORM_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    STATUS_ALIAS_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    REVIEWED_AT_FIELD_NUMBER: _ClassVar[int]
    REVIEW_REASON_FIELD_NUMBER: _ClassVar[int]
    AUTO_APPROVED_FIELD_NUMBER: _ClassVar[int]
    MINECRAFT_ACCOUNT_TYPE_FIELD_NUMBER: _ClassVar[int]
    MINECRAFT_IDENTITY_STATE_FIELD_NUMBER: _ClassVar[int]
    MINECRAFT_NICK_FIELD_NUMBER: _ClassVar[int]
    MINECRAFT_UUID_FIELD_NUMBER: _ClassVar[int]
    DISCORD_NAME_FIELD_NUMBER: _ClassVar[int]
    APPLICATION_URL_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_FIELD_NUMBER: _ClassVar[int]
    id: int
    user_id: int
    server_id: int
    form_id: int
    status: str
    status_alias: str
    created_at: str
    reviewed_at: str
    review_reason: str
    auto_approved: bool
    minecraft_account_type: str
    minecraft_identity_state: str
    minecraft_nick: str
    minecraft_uuid: str
    discord_name: str
    application_url: str
    payload: _containers.ScalarMap[str, str]
    def __init__(self, id: _Optional[int] = ..., user_id: _Optional[int] = ..., server_id: _Optional[int] = ..., form_id: _Optional[int] = ..., status: _Optional[str] = ..., status_alias: _Optional[str] = ..., created_at: _Optional[str] = ..., reviewed_at: _Optional[str] = ..., review_reason: _Optional[str] = ..., auto_approved: bool = ..., minecraft_account_type: _Optional[str] = ..., minecraft_identity_state: _Optional[str] = ..., minecraft_nick: _Optional[str] = ..., minecraft_uuid: _Optional[str] = ..., discord_name: _Optional[str] = ..., application_url: _Optional[str] = ..., payload: _Optional[_Mapping[str, str]] = ...) -> None: ...
