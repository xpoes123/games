import pytest

from src.rooms import ROOM_CODE_LEN, RoomRegistry


@pytest.mark.asyncio
async def test_create_returns_unique_codes():
    reg = RoomRegistry()
    a = await reg.create()
    b = await reg.create()
    assert a.code != b.code
    assert len(a.code) == ROOM_CODE_LEN
    assert a.code.isupper()


@pytest.mark.asyncio
async def test_get_is_case_insensitive():
    reg = RoomRegistry()
    room = await reg.create()
    assert reg.get(room.code.lower()) is room
    assert reg.get("ZZZZ") is None


@pytest.mark.asyncio
async def test_drop_if_empty_removes_room():
    reg = RoomRegistry()
    room = await reg.create()
    assert reg.get(room.code) is room
    await reg.drop_if_empty(room.code)
    assert reg.get(room.code) is None
