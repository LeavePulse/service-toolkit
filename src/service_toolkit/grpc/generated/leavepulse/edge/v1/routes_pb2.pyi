from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class GetRoutesRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class RouteItem(_message.Message):
    __slots__ = ("source_host", "target_url")
    SOURCE_HOST_FIELD_NUMBER: _ClassVar[int]
    TARGET_URL_FIELD_NUMBER: _ClassVar[int]
    source_host: str
    target_url: str
    def __init__(self, source_host: _Optional[str] = ..., target_url: _Optional[str] = ...) -> None: ...

class RoutesResponse(_message.Message):
    __slots__ = ("items", "total")
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    TOTAL_FIELD_NUMBER: _ClassVar[int]
    items: _containers.RepeatedCompositeFieldContainer[RouteItem]
    total: int
    def __init__(self, items: _Optional[_Iterable[_Union[RouteItem, _Mapping]]] = ..., total: _Optional[int] = ...) -> None: ...
