import asyncio

import pytest
from app.temporal import client as temporal_client


class _FakeClient:
    pass


@pytest.fixture
def connects(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    async def fake_connect(host: str) -> _FakeClient:
        calls.append(host)
        await asyncio.sleep(0)
        return _FakeClient()

    monkeypatch.setattr(temporal_client.Client, "connect", fake_connect)
    monkeypatch.setattr(temporal_client, "_cached", None)
    return calls


async def test_client_is_created_once_per_loop(connects: list[str]) -> None:
    first = await temporal_client.connect()
    second = await temporal_client.connect()
    assert first is second
    assert len(connects) == 1


async def test_parallel_first_requests_share_one_connection(connects: list[str]) -> None:
    clients = await asyncio.gather(*(temporal_client.connect() for _ in range(5)))
    assert len({id(item) for item in clients}) == 1
    assert len(connects) == 1


async def test_other_address_gets_its_own_client(
    connects: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    first = await temporal_client.connect()
    monkeypatch.setattr(temporal_client.settings, "temporal_host", "other-host:7233")
    second = await temporal_client.connect()
    assert first is not second
    assert connects[-1] == "other-host:7233"


async def test_client_from_a_closed_loop_is_not_reused(
    connects: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    stale_loop = asyncio.new_event_loop()
    stale_loop.close()
    monkeypatch.setattr(
        temporal_client,
        "_cached",
        (stale_loop, temporal_client.settings.temporal_host, _FakeClient()),
    )
    fresh = await temporal_client.connect()
    assert len(connects) == 1
    assert fresh is temporal_client._cached[2]
