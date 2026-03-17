from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Mapping as _Mapping, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class SendNotificationRequest(_message.Message):
    __slots__ = ("bot_id", "owner_user_id", "channel_id", "provider_id", "label", "payload")
    class PayloadEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    BOT_ID_FIELD_NUMBER: _ClassVar[int]
    OWNER_USER_ID_FIELD_NUMBER: _ClassVar[int]
    CHANNEL_ID_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_ID_FIELD_NUMBER: _ClassVar[int]
    LABEL_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_FIELD_NUMBER: _ClassVar[int]
    bot_id: int
    owner_user_id: int
    channel_id: str
    provider_id: str
    label: str
    payload: _containers.ScalarMap[str, str]
    def __init__(self, bot_id: _Optional[int] = ..., owner_user_id: _Optional[int] = ..., channel_id: _Optional[str] = ..., provider_id: _Optional[str] = ..., label: _Optional[str] = ..., payload: _Optional[_Mapping[str, str]] = ...) -> None: ...

class SendNotificationResponse(_message.Message):
    __slots__ = ("task_id", "subscription_id")
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    SUBSCRIPTION_ID_FIELD_NUMBER: _ClassVar[int]
    task_id: str
    subscription_id: str
    def __init__(self, task_id: _Optional[str] = ..., subscription_id: _Optional[str] = ...) -> None: ...
