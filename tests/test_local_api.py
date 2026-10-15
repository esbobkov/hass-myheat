"""Tests for the local MY HEAT UI API client."""

import asyncio
import json
from copy import deepcopy

import pytest

from custom_components.myheat import local_api
from custom_components.myheat.const import LOCAL_SENTINEL
from custom_components.myheat.hybrid_api import normalize_local_device_info
from custom_components.myheat.local_api import (
    LocalApiClient,
    LocalAuthError,
    LocalResponseError,
    LocalVerificationError,
)

from .const import MOCK_LOCAL_GET_STATE, MOCK_LOCAL_OBJ_STATE


class FakeHeaders(dict):
    """Small subset of aiohttp response headers used by the client."""

    def getall(self, key, default=None):
        value = self.get(key, default or [])
        if isinstance(value, list):
            return value
        return [value]


class FakeResponse:
    def __init__(self, payload, *, status=200, headers=None, raw=None):
        self._payload = payload
        self._raw = raw
        self.status = status
        self.headers = FakeHeaders(headers or {})

    async def read(self):
        if self._raw is not None:
            return self._raw
        return json.dumps(self._payload).encode("utf-8")

    async def json(self, *, content_type=None):  # pylint: disable=unused-argument
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def post(self, url, *, headers, json):
        self.requests.append(
            {
                "method": "POST",
                "url": url,
                "headers": headers,
                "json": json,
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def get(self, url, *, headers):
        self.requests.append({"method": "GET", "url": url, "headers": headers})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def local_client(session) -> LocalApiClient:
    return LocalApiClient(
        host="192.0.2.10",
        username="myheat",
        password="myheat",
        session=session,
    )


async def test_local_login_stores_cookie_and_sends_it():
    session = FakeSession(
        [
            FakeResponse(
                {"status": True},
                headers={"Set-Cookie": "EspSessId=session-id; Max-Age=86400; Path=/;"},
            ),
            FakeResponse(MOCK_LOCAL_OBJ_STATE),
        ]
    )
    client = local_client(session)

    await client.async_login()
    assert client.authenticated

    await client.async_get_obj_state()
    assert "Cookie" not in session.requests[0]["headers"]
    assert session.requests[1]["headers"]["Cookie"] == "EspSessId=session-id"


@pytest.mark.parametrize("status", [1, "1", "true"])
async def test_local_login_accepts_numeric_and_string_status(status):
    """Живой контроллер отвечает {"status": 1} — не логическим значением."""
    session = FakeSession(
        [
            FakeResponse(
                {"status": status},
                headers={"Set-Cookie": "EspSessId=session-id; Max-Age=86400; Path=/;"},
            ),
        ]
    )
    client = local_client(session)

    await client.async_login()
    assert client.authenticated


@pytest.mark.parametrize("status", [0, "0", False])
async def test_local_login_rejects_zero_status(status):
    session = FakeSession([FakeResponse({"status": status})])
    client = local_client(session)

    with pytest.raises(LocalAuthError):
        await client.async_login()


async def test_local_failed_login_raises_auth_error():
    session = FakeSession([FakeResponse({"status": False})])
    client = local_client(session)

    with pytest.raises(LocalAuthError):
        await client.async_login()


async def test_normalize_local_device_info_maps_objects_and_sanitizes_regkey():
    data = normalize_local_device_info(MOCK_LOCAL_OBJ_STATE, MOCK_LOCAL_GET_STATE)

    assert data["dataActual"] is True
    assert data["severity"] == 1
    assert data["local"]["simSignal"] == 71
    assert data["local"]["gsmBalance"] == 137.7
    assert "regkey" not in data["local"]["deviceState"]

    room = data["envs"][0]
    assert room["id"] == 67
    assert room["type"] == "room_temperature"
    assert room["value"] == 22.5
    assert room["target"] == 23.5
    assert room["localControl"]["curve"] is True

    unavailable = data["envs"][1]
    assert unavailable["value"] is None

    humidity_state = deepcopy(MOCK_LOCAL_OBJ_STATE)
    humidity_state["envs"] = [
        {
            "n": "Humidity",
            "i": 69,
            "t": 109,
            "st": {"p1": 47.2},
            "s": {},
        }
    ]
    humidity = normalize_local_device_info(humidity_state)["envs"][0]
    assert humidity["type"] == "room_humidity"
    assert humidity["value"] == 47.2

    heater = data["heaters"][0]
    assert heater["disabled"] is False
    assert heater["flowTemp"] == 51
    assert heater["localControl"]["enabled"] is True


async def test_local_obj_state_schema_rejects_missing_required_arrays():
    session = FakeSession([FakeResponse({"heaters": []})])
    client = local_client(session)
    client._session_id = "session-id"  # pylint: disable=protected-access

    with pytest.raises(LocalResponseError):
        await client.async_get_obj_state()


async def test_local_heater_enabled_payload_and_verification():
    obj_state = deepcopy(MOCK_LOCAL_OBJ_STATE)
    updated_state = deepcopy(MOCK_LOCAL_OBJ_STATE)
    updated_state["heaters"][0]["s"]["p3013"] = "1"
    session = FakeSession(
        [
            FakeResponse({"status": 1}),
            FakeResponse(updated_state),
        ]
    )
    client = local_client(session)
    client._session_id = "session-id"  # pylint: disable=protected-access
    client._obj_state = obj_state  # pylint: disable=protected-access

    await client.async_set_heater_enabled(obj_id=45, enabled=False)

    assert session.requests[0]["json"] == {
        "id": 45,
        "target": "heater",
        "value": 1,
    }
    assert session.requests[0]["headers"]["Cookie"] == "EspSessId=session-id"


async def test_local_env_target_payload_and_verification():
    updated_state = deepcopy(MOCK_LOCAL_OBJ_STATE)
    updated_state["envs"][0]["s"]["p3008"] = "25.1"
    session = FakeSession(
        [
            FakeResponse({"status": 1}),
            FakeResponse(updated_state),
        ]
    )
    client = local_client(session)
    client._session_id = "session-id"  # pylint: disable=protected-access
    client._obj_state = deepcopy(
        MOCK_LOCAL_OBJ_STATE
    )  # pylint: disable=protected-access

    await client.async_set_env_goal(obj_id=67, goal=25.1)

    assert session.requests[0]["json"] == {
        "id": 67,
        "target": "env",
        "value": 25.1,
        "curve": LOCAL_SENTINEL,
    }


async def test_local_env_curve_payload_and_verification():
    updated_state = deepcopy(MOCK_LOCAL_OBJ_STATE)
    updated_state["envs"][0]["s"]["p3022"] = "1"
    session = FakeSession(
        [
            FakeResponse({"status": 1}),
            FakeResponse(updated_state),
        ]
    )
    client = local_client(session)
    client._session_id = "session-id"  # pylint: disable=protected-access
    client._obj_state = deepcopy(
        MOCK_LOCAL_OBJ_STATE
    )  # pylint: disable=protected-access

    await client.async_set_env_curve(obj_id=67, curve=1)

    assert session.requests[0]["json"] == {
        "id": 67,
        "target": "env",
        "value": LOCAL_SENTINEL,
        "curve": 1,
    }


async def test_local_eng_goal_payload_and_verification():
    updated_state = deepcopy(MOCK_LOCAL_OBJ_STATE)
    updated_state["engs"][0]["s"]["p3008"] = "1"
    session = FakeSession(
        [
            FakeResponse({"status": 1}),
            FakeResponse(updated_state),
        ]
    )
    client = local_client(session)
    client._session_id = "session-id"  # pylint: disable=protected-access
    client._obj_state = deepcopy(
        MOCK_LOCAL_OBJ_STATE
    )  # pylint: disable=protected-access

    await client.async_set_eng_goal(obj_id=123, goal=1)

    assert session.requests[0]["json"] == {
        "id": 123,
        "target": "eng",
        "value": 1,
    }


async def test_local_heating_mode_payload_refreshes_state():
    session = FakeSession(
        [
            FakeResponse({"status": 1}),
            FakeResponse(MOCK_LOCAL_OBJ_STATE),
        ]
    )
    client = local_client(session)
    client._session_id = "session-id"  # pylint: disable=protected-access
    client._obj_state = deepcopy(
        MOCK_LOCAL_OBJ_STATE
    )  # pylint: disable=protected-access

    await client.async_set_heating_mode(mode_id=1)

    assert session.requests[0]["json"] == {
        "action": "setHeatingMode",
        "mode": 1,
        "schedule": -1,
    }


async def test_local_write_verification_mismatch_raises():
    session = FakeSession(
        [
            FakeResponse({"status": 1}),
            FakeResponse(MOCK_LOCAL_OBJ_STATE),
        ]
    )
    client = local_client(session)
    client._session_id = "session-id"  # pylint: disable=protected-access
    client._obj_state = deepcopy(
        MOCK_LOCAL_OBJ_STATE
    )  # pylint: disable=protected-access

    with pytest.raises(LocalVerificationError):
        await client.async_set_heater_enabled(obj_id=45, enabled=False)


COOKIE = {"Set-Cookie": "EspSessId=session-id; Max-Age=86400; Path=/;"}


@pytest.fixture(autouse=True)
def _no_async_wait(monkeypatch):
    monkeypatch.setattr(local_api, "ASYNC_POLL_INTERVAL", 0)


async def test_get_obj_state_uses_async_flow_like_the_controller_ui():
    """По HAR веб-интерфейса: POST .../async -> 202 {id}, затем опрос getAsyncRequest."""
    session = FakeSession(
        [
            FakeResponse({"status": 1}, headers=COOKIE),
            FakeResponse({"id": "6dda6b37"}, status=202),
            FakeResponse({"ar_status": 0}),
            FakeResponse(MOCK_LOCAL_OBJ_STATE),
        ]
    )
    client = local_client(session)

    state = await client.async_get_obj_state()

    assert state["heaters"]
    assert session.requests[1]["method"] == "POST"
    assert session.requests[1]["url"].endswith("/api/getObjState/async")
    assert session.requests[1]["headers"]["X-Requested-With"] == "XMLHttpRequest"
    assert session.requests[2]["method"] == "GET"
    assert "/api/getAsyncRequest?id=6dda6b37&_=" in session.requests[2]["url"]
    assert session.requests[3]["headers"]["Cookie"] == "EspSessId=session-id"


async def test_get_obj_state_falls_back_to_sync_when_async_path_is_missing():
    session = FakeSession(
        [
            FakeResponse({"status": 1}, headers=COOKIE),
            FakeResponse({}, status=404),
            FakeResponse(MOCK_LOCAL_OBJ_STATE),
        ]
    )
    client = local_client(session)

    await client.async_get_obj_state()

    assert session.requests[2]["url"].endswith("/api/getObjState")


async def test_async_request_error_status_raises():
    session = FakeSession(
        [
            FakeResponse({"status": 1}, headers=COOKIE),
            FakeResponse({"id": "x"}, status=202),
            FakeResponse({"ar_status": -1}),
        ]
    )
    client = local_client(session)

    with pytest.raises(LocalResponseError):
        await client.async_get_obj_state()


async def test_login_with_empty_session_cookie_is_an_auth_error():
    """Контроллер без свободных сессий отвечает status 1, но пустым EspSessId."""
    session = FakeSession(
        [
            FakeResponse(
                {"status": 1}, headers={"Set-Cookie": "EspSessId=; Max-Age=86400"}
            )
        ]
    )
    client = local_client(session)

    with pytest.raises(LocalAuthError):
        await client.async_login()
    assert not client.authenticated


async def test_forbidden_triggers_one_relogin(monkeypatch):
    monkeypatch.setattr(local_api, "RELOGIN_MIN_INTERVAL", 0)
    session = FakeSession(
        [
            FakeResponse({"status": 1}, headers=COOKIE),
            FakeResponse({"status": -1}, status=403),
            FakeResponse(
                {"status": 1},
                headers={"Set-Cookie": "EspSessId=second; Max-Age=86400; Path=/;"},
            ),
            FakeResponse(MOCK_LOCAL_OBJ_STATE),
        ]
    )
    client = local_client(session)

    await client.async_get_obj_state()

    assert session.requests[-1]["headers"]["Cookie"] == "EspSessId=second"


async def test_forbidden_relogin_is_rate_limited():
    """Не входить в цикле: каждый вход занимает слот сессии контроллера."""
    session = FakeSession(
        [
            FakeResponse({"status": 1}, headers=COOKIE),
            FakeResponse({"status": -1}, status=403),
        ]
    )
    client = local_client(session)

    with pytest.raises(LocalResponseError):
        await client.async_get_obj_state()
    assert len(session.requests) == 2


async def test_obj_state_with_a_cut_cyrillic_letter_is_accepted():
    """Контроллер режет имена по байтам: тело невалидно как UTF-8 (проба владельца)."""
    raw = json.dumps(MOCK_LOCAL_OBJ_STATE, ensure_ascii=False).encode("utf-8")
    # Первый байт кириллической буквы без второго перед обычным символом.
    raw = raw.replace(b'"n": "', b'"n": "\xd0', 1)
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")
    session = FakeSession(
        [
            FakeResponse({"status": 1}, headers=COOKIE),
            FakeResponse({"id": "x"}, status=202),
            FakeResponse(None, raw=raw),
        ]
    )
    client = local_client(session)

    result = await client.async_get_obj_state()

    assert result["heaters"]


async def test_login_sends_browser_like_headers():
    session = FakeSession([FakeResponse({"status": 1}, headers=COOKIE)])
    client = local_client(session)

    await client.async_login()

    headers = session.requests[0]["headers"]
    assert headers["Origin"] == "http://192.0.2.10"
    assert headers["Referer"].endswith("/login.html")
    assert headers["X-Requested-With"] == "XMLHttpRequest"
    assert "Mozilla" in headers["User-Agent"]


async def test_concurrent_obj_state_requests_are_serialized():
    """Опрос и запись не должны идти на контроллер одновременно (ESP отвечает оборванным JSON)."""
    session = FakeSession(
        [
            FakeResponse({"status": 1}, headers=COOKIE),
            FakeResponse({"id": "a"}, status=202),
            FakeResponse({"ar_status": 0}),
            FakeResponse(MOCK_LOCAL_OBJ_STATE),
            FakeResponse({"id": "b"}, status=202),
            FakeResponse({"ar_status": 0}),
            FakeResponse(MOCK_LOCAL_OBJ_STATE),
        ]
    )
    client = local_client(session)

    await asyncio.gather(client.async_get_obj_state(), client.async_get_obj_state())

    urls = [r["url"].split("/api/")[1].split("?")[0] for r in session.requests]
    assert urls == [
        "login",
        "getObjState/async",
        "getAsyncRequest",
        "getAsyncRequest",
        "getObjState/async",
        "getAsyncRequest",
        "getAsyncRequest",
    ]


async def test_broken_async_result_is_retried_once():
    session = FakeSession(
        [
            FakeResponse({"status": 1}, headers=COOKIE),
            FakeResponse({"id": "a"}, status=202),
            FakeResponse(None, raw=b'{"deviceFlags": 1, "envs": [{"n": "'),
            FakeResponse({"id": "b"}, status=202),
            FakeResponse(MOCK_LOCAL_OBJ_STATE),
        ]
    )
    client = local_client(session)

    state = await client.async_get_obj_state()

    assert state["heaters"]
