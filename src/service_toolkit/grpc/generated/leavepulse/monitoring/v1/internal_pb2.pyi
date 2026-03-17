from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class GetPlayerStatsRequest(_message.Message):
    __slots__ = ("user_id", "server_id")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    SERVER_ID_FIELD_NUMBER: _ClassVar[int]
    user_id: int
    server_id: int
    def __init__(self, user_id: _Optional[int] = ..., server_id: _Optional[int] = ...) -> None: ...

class PlayerStatsResponse(_message.Message):
    __slots__ = ("found", "total_playtime_seconds", "server_playtime_seconds", "source")
    FOUND_FIELD_NUMBER: _ClassVar[int]
    TOTAL_PLAYTIME_SECONDS_FIELD_NUMBER: _ClassVar[int]
    SERVER_PLAYTIME_SECONDS_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    found: bool
    total_playtime_seconds: int
    server_playtime_seconds: int
    source: str
    def __init__(self, found: bool = ..., total_playtime_seconds: _Optional[int] = ..., server_playtime_seconds: _Optional[int] = ..., source: _Optional[str] = ...) -> None: ...

class GetLandingStatsRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class LandingStatsResponse(_message.Message):
    __slots__ = ("players_with_profile", "total_playtime_hours", "registered_profiles")
    PLAYERS_WITH_PROFILE_FIELD_NUMBER: _ClassVar[int]
    TOTAL_PLAYTIME_HOURS_FIELD_NUMBER: _ClassVar[int]
    REGISTERED_PROFILES_FIELD_NUMBER: _ClassVar[int]
    players_with_profile: int
    total_playtime_hours: int
    registered_profiles: int
    def __init__(self, players_with_profile: _Optional[int] = ..., total_playtime_hours: _Optional[int] = ..., registered_profiles: _Optional[int] = ...) -> None: ...
