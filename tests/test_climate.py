"""Test MyHeat climate entities."""

from copy import deepcopy
from unittest.mock import call, patch

from homeassistant.components.climate import (
    ATTR_HVAC_ACTION,
    ATTR_HVAC_MODE,
    ATTR_MAX_TEMP,
    ATTR_MIN_TEMP,
    ATTR_PRESET_MODE,
    ATTR_PRESET_MODES,
    DOMAIN as CLIMATE_DOMAIN,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
    PRESET_HOME,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_PRESET_MODE,
    SERVICE_SET_TEMPERATURE,
)
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    ATTR_TEMPERATURE,
)

from .const import MOCK_GET_DEVICE_INFO
from .helpers import setup_mock_entry, state_by_name


async def test_climate_entities(hass, bypass_get_device_info):
    """Test climate entities are created from room and floor temperature envs."""
    await setup_mock_entry(hass)

    assert len(hass.states.async_entity_ids(CLIMATE_DOMAIN)) == 5

    cafe = state_by_name(hass, CLIMATE_DOMAIN, "test_device Кафе")
    assert cafe.state == HVACMode.HEAT
    assert cafe.attributes["current_temperature"] == 24.8
    assert cafe.attributes["temperature"] == 23
    assert cafe.attributes[ATTR_HVAC_ACTION] == HVACAction.IDLE

    floor = state_by_name(hass, CLIMATE_DOMAIN, "test_device Теплый пол")
    assert floor.state == HVACMode.HEAT
    assert floor.attributes["current_temperature"] == 26.5
    assert floor.attributes["temperature"] == 27
    assert floor.attributes[ATTR_HVAC_ACTION] == HVACAction.HEATING

    accounting = state_by_name(hass, CLIMATE_DOMAIN, "test_device Бухгалтерия")
    assert accounting.state == HVACMode.OFF
    assert accounting.attributes["current_temperature"] == 29.8
    assert accounting.attributes["temperature"] is None
    assert accounting.attributes[ATTR_HVAC_ACTION] == HVACAction.OFF


async def test_climate_services(hass, bypass_get_device_info):
    """Test climate services call MyHeat API methods."""
    await setup_mock_entry(hass)
    cafe = state_by_name(hass, CLIMATE_DOMAIN, "test_device Кафе")

    with patch("custom_components.myheat.MhApiClient.async_set_env_goal") as goal_func:
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_TEMPERATURE,
            service_data={
                ATTR_ENTITY_ID: cafe.entity_id,
                ATTR_TEMPERATURE: 21.5,
            },
            blocking=True,
        )
        assert goal_func.call_args == call(obj_id=22, goal=21.5, change_mode=True)

        goal_func.reset_mock()

        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_HVAC_MODE,
            service_data={
                ATTR_ENTITY_ID: cafe.entity_id,
                ATTR_HVAC_MODE: HVACMode.OFF,
            },
            blocking=True,
        )
        assert goal_func.call_args == call(obj_id=22, goal=None)

        goal_func.reset_mock()

        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_HVAC_MODE,
            service_data={
                ATTR_ENTITY_ID: cafe.entity_id,
                ATTR_HVAC_MODE: HVACMode.HEAT,
            },
            blocking=True,
        )
        assert goal_func.call_args == call(obj_id=22, goal=23, change_mode=True)

    with patch(
        "custom_components.myheat.MhApiClient.async_set_heating_mode"
    ) as mode_func:
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_PRESET_MODE,
            service_data={
                ATTR_ENTITY_ID: cafe.entity_id,
                ATTR_PRESET_MODE: PRESET_HOME,
            },
            blocking=True,
        )
        assert mode_func.call_args == call(mode_id=1)


def _device_info_with_common_envs() -> dict:
    """Device info where zones are reported as common_temperature."""
    data = deepcopy(MOCK_GET_DEVICE_INFO["data"])
    data["envs"] += [
        {
            "id": 30,
            "type": "common_temperature",
            "name": "Детская",
            "value": 24.4,
            "target": 24,
            "demand": True,
            "severity": 1,
            "severityDesc": "Нормальное состояние.",
        },
        {
            "id": 31,
            "type": "common_temperature",
            "name": "Кухня",
            "value": 25.0,
            "target": None,
            "demand": False,
            "severity": 1,
            "severityDesc": "Нормальное состояние.",
        },
    ]
    return data


async def test_common_temperature_with_goal_is_climate(hass):
    """A common_temperature env with a goal gets a climate entity, one without does not."""
    with patch(
        "custom_components.myheat.MhApiClient.async_get_device_info",
        return_value=_device_info_with_common_envs(),
    ):
        await setup_mock_entry(hass)

    assert len(hass.states.async_entity_ids(CLIMATE_DOMAIN)) == 6

    room = state_by_name(hass, CLIMATE_DOMAIN, "test_device Детская")
    assert room.state == HVACMode.HEAT
    assert room.attributes["current_temperature"] == 24.4
    assert room.attributes["temperature"] == 24
    assert room.attributes[ATTR_HVAC_ACTION] == HVACAction.HEATING
    assert room.attributes[ATTR_MIN_TEMP] == 15
    assert room.attributes[ATTR_MAX_TEMP] == 30
    assert ATTR_PRESET_MODES not in room.attributes
    assert not (
        room.attributes[ATTR_SUPPORTED_FEATURES] & ClimateEntityFeature.PRESET_MODE
    )

    names = [
        state.attributes.get("friendly_name")
        for state in hass.states.async_all(CLIMATE_DOMAIN)
    ]
    assert "test_device Кухня" not in names

    # The plain sensors stay, so existing dashboards and templates keep working.
    assert state_by_name(hass, SENSOR_DOMAIN, "test_device Детская").state == "24.4"
    assert state_by_name(hass, SENSOR_DOMAIN, "test_device Кухня").state == "25.0"


async def test_common_temperature_climate_sets_goal(hass):
    """Setting a temperature on a common_temperature climate sets the env goal."""
    with patch(
        "custom_components.myheat.MhApiClient.async_get_device_info",
        return_value=_device_info_with_common_envs(),
    ):
        await setup_mock_entry(hass)
        room = state_by_name(hass, CLIMATE_DOMAIN, "test_device Детская")

        with patch(
            "custom_components.myheat.MhApiClient.async_set_env_goal"
        ) as goal_func:
            await hass.services.async_call(
                CLIMATE_DOMAIN,
                SERVICE_SET_TEMPERATURE,
                service_data={ATTR_ENTITY_ID: room.entity_id, ATTR_TEMPERATURE: 24.5},
                blocking=True,
            )
            assert goal_func.call_args == call(obj_id=30, goal=24.5)
