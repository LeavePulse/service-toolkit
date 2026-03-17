from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class Empty(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class Pagination(_message.Message):
    __slots__ = ("page", "limit", "total")
    PAGE_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    TOTAL_FIELD_NUMBER: _ClassVar[int]
    page: int
    limit: int
    total: int
    def __init__(self, page: _Optional[int] = ..., limit: _Optional[int] = ..., total: _Optional[int] = ...) -> None: ...
