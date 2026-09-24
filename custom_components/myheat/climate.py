"""Climate platform for MyHeat."""

from homeassistant.components.climate import (
    PRESET_AWAY,
    PRESET_ECO,
    PRESET_HOME,
    PRESET_NONE,
    PRESET_SLEEP,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.components.climate import PRESET_ACTIVITY  # noqa: F401
from homeassistant.components.climate import PRESET_BOOST  # noqa: F401
from homeassistant.components.climate import PRESET_COMFORT  # noqa: F401
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import CLIMATE_ENV_TYPES, ENV_TYPE_COMMON_TEMPERATURE
from .coordinator import MhConfigEntry, MhDataUpdateCoordinator
from .entity import MhEnvEntity

PRESET_TO_ID = {
    # PRESET_ACTIVITY: 0,
    PRESET_AWAY: 3,  # Режим: Отпуск
    # PRESET_BOOST: 0,
    # PRESET_COMFORT: 0,
    PRESET_ECO: 2,  # Режим: На работе
    PRESET_HOME: 1,  # Режим: Дома
    PRESET_NONE: 0,
    PRESET_SLEEP: 4,  # Режим: Лето
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MhConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Setup climate platform."""
    coordinator: MhDataUpdateCoordinator = entry.runtime_data

    entities: list[MhEnvClimate] = []
    for env in coordinator.data.get("envs", []):
        if env.get("type") in CLIMATE_ENV_TYPES:
            entities.append(MhEnvClimate(coordinator, entry, env))
        elif is_controllable_common_env(env):
            entities.append(MhCommonEnvClimate(coordinator, entry, env))

    async_add_entities(entities)


def is_controllable_common_env(env: dict) -> bool:
    """Return True for a common_temperature env that has a goal.

    Some controllers report every room and floor sensor as common_temperature,
    including zones that have a goal and are regulated by the controller.
    Without a climate entity such zones can only be watched, not controlled.
    A common_temperature env without a goal is a plain sensor and stays one.
    """
    return (
        env.get("type") == ENV_TYPE_COMMON_TEMPERATURE and env.get("target") is not None
    )


class MhEnvClimate(MhEnvEntity, ClimateEntity):
    """myheat Climate class."""

    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.PRESET_MODE
    )

    _attr_fan_mode = None
    _attr_fan_modes = []

    _attr_hvac_action = None
    _attr_hvac_mode = HVACMode.OFF
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]

    _attr_is_aux_heat = False

    _attr_target_temperature_high = 40
    _attr_target_temperature_low = 15
    _attr_target_temperature_step = 0.5

    _attr_temperature_unit = UnitOfTemperature.CELSIUS

    def __init__(
        self,
        coordinator: MhDataUpdateCoordinator,
        config_entry: MhConfigEntry,
        env: dict,
    ):
        super().__init__(coordinator, config_entry, env)

        self._attr_current_humidity = None
        self._attr_current_temperature = None

        # self._attr_max_humidity = DEFAULT_MAX_HUMIDITY
        # self._attr_min_humidity: int = DEFAULT_MIN_HUMIDITY
        # self._attr_max_temp: float
        # self._attr_min_temp: float

        # self._attr_precision: float
        self._attr_preset_mode = PRESET_NONE
        self._attr_preset_modes = list(PRESET_TO_ID.keys())

        # self._attr_swing_mode: str | None
        # self._attr_swing_modes: list[str] | None

        self._attr_target_temperature = None
        # self._attr_target_humidity = None

        self._update_state_attrs()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target hvac mode."""

        if hvac_mode == HVACMode.OFF:
            goal = None
        else:
            goal = self._attr_target_temperature
            if goal is None:
                goal = 24  # NOTE(vooon): we need some reasonable value to turn on the heater and i like 22-26.

        await self.coordinator.api.async_set_env_goal(obj_id=self.env_id, goal=goal)
        await self.coordinator.async_request_refresh()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set new preset mode."""
        mode_id = PRESET_TO_ID[preset_mode]
        await self.coordinator.api.async_set_heating_mode(mode_id=mode_id)
        await self.coordinator.async_request_refresh()

    async def async_set_temperature(self, **kwargs) -> None:
        """Set new target temperature."""
        goal = kwargs.get("temperature", 0.0)
        await self.coordinator.api.async_set_env_goal(obj_id=self.env_id, goal=goal)
        await self.coordinator.async_request_refresh()

    def _update_state_attrs(self) -> None:
        """Update entity state attributes from coordinator data."""
        e = self.get_env()

        self._attr_current_temperature = e.get("value")
        self._attr_target_temperature = e.get("target")

        self._attr_hvac_action = (
            (HVACAction.HEATING if e.get("demand", False) else HVACAction.IDLE)
            if self._attr_target_temperature is not None
            else HVACAction.OFF
        )

        self._attr_hvac_mode = (
            HVACMode.HEAT if self._attr_target_temperature is not None else HVACMode.OFF
        )

    @callback
    def _handle_coordinator_update(self):
        """Get the latest state from the thermostat."""
        self._update_state_attrs()
        self.async_write_ha_state()


class MhCommonEnvClimate(MhEnvClimate):
    """Climate for a common_temperature env that has a goal.

    The API does not tell whether such an env measures air or floor, so the
    goal range is conservative: 30 °C is already warm for air and is the
    upper bound usually allowed for a floor covering.

    Preset modes are not offered: MyHeat heating modes are device-wide, and a
    preset selector on every zone would switch the whole controller. The
    plain sensor for the same env is still created by the sensor platform.
    """

    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE

    _attr_min_temp = 15
    _attr_max_temp = 30

    def __init__(
        self,
        coordinator: MhDataUpdateCoordinator,
        config_entry: MhConfigEntry,
        env: dict,
    ):
        super().__init__(coordinator, config_entry, env)

        self._attr_preset_mode = None
        self._attr_preset_modes = None
