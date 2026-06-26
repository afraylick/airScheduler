"""Air Scheduler integration."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, time, timedelta
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_track_point_in_time
import homeassistant.util.dt as dt_util

from .const import (
    CONF_APPLY_ON_START,
    CONF_CONFIG,
    CONF_DAYS,
    CONF_ENABLED,
    CONF_HVAC_MODE,
    CONF_PROFILE,
    CONF_PROFILES,
    CONF_SCHEDULES,
    CONF_TIME,
    DAY_ALIASES,
    DOMAIN,
    NAME,
    SERVICE_APPLY_PROFILE,
    SERVICE_RELOAD,
    SERVICE_SET_CONFIG,
)

_LOGGER = logging.getLogger(__name__)

DATA_SCHEDULERS = "schedulers"
PROFILES = ("home", "away", "sleep")

DEFAULT_CONFIG: dict[str, Any] = {
    CONF_APPLY_ON_START: True,
    CONF_PROFILES: {
        "home": {},
        "away": {},
        "sleep": {},
    },
    CONF_SCHEDULES: [],
}


def _plain_data(value: Any) -> Any:
    """Convert Home Assistant read-only config data into plain containers."""
    if isinstance(value, Mapping):
        return {key: _plain_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain_data(item) for item in value]
    if isinstance(value, tuple):
        return [_plain_data(item) for item in value]
    return value


def _normalized_config(config: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalize stored config into the expected per-thermostat shape."""
    source = _plain_data(config or {})
    entity_id = source.get(ATTR_ENTITY_ID)
    if not entity_id and source.get("entities"):
        entity_id = source["entities"][0]

    normalized = _plain_data(DEFAULT_CONFIG)
    normalized.update(source)
    normalized.pop("entities", None)
    if entity_id:
        normalized[ATTR_ENTITY_ID] = entity_id

    normalized[CONF_PROFILES] = normalized.get(CONF_PROFILES) or {}
    for profile in PROFILES:
        settings = normalized[CONF_PROFILES].get(profile, {})
        if entity_id and isinstance(settings, dict) and entity_id in settings:
            settings = settings[entity_id]
        elif isinstance(settings, dict) and "default" in settings:
            settings = settings["default"]
        normalized[CONF_PROFILES][profile] = settings if isinstance(settings, dict) else {}

    normalized[CONF_SCHEDULES] = list(normalized.get(CONF_SCHEDULES) or [])
    for schedule in normalized[CONF_SCHEDULES]:
        if isinstance(schedule, dict):
            schedule.pop("entities", None)
    return normalized


SET_CONFIG_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CONFIG): dict,
        vol.Optional(ATTR_ENTITY_ID): cv.entity_id,
    }
)

APPLY_PROFILE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PROFILE): cv.string,
        vol.Optional(ATTR_ENTITY_ID): cv.entity_ids,
    }
)


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up Air Scheduler services."""
    hass.data.setdefault(DOMAIN, {DATA_SCHEDULERS: {}})

    async def async_set_config(call: ServiceCall) -> None:
        schedulers = _matching_schedulers(hass, call.data.get(ATTR_ENTITY_ID))
        if not schedulers:
            _LOGGER.warning("No Air Scheduler thermostat matched set_config target")
            return
        for scheduler in schedulers:
            await scheduler.async_set_config(call.data[CONF_CONFIG])

    async def async_apply_profile(call: ServiceCall) -> None:
        schedulers = _matching_schedulers(hass, call.data.get(ATTR_ENTITY_ID))
        if not schedulers:
            _LOGGER.warning("No Air Scheduler thermostat matched apply_profile target")
            return
        for scheduler in schedulers:
            await scheduler.async_apply_profile(call.data[CONF_PROFILE])

    async def async_reload(call: ServiceCall) -> None:
        for scheduler in _all_schedulers(hass):
            await scheduler.async_reload()

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_CONFIG,
        async_set_config,
        schema=SET_CONFIG_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_APPLY_PROFILE,
        async_apply_profile,
        schema=APPLY_PROFILE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RELOAD,
        async_reload,
    )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Air Scheduler from a config entry."""
    config = _normalized_config(entry.options or entry.data or DEFAULT_CONFIG)
    scheduler = AirScheduler(hass, entry, config)
    hass.data.setdefault(DOMAIN, {DATA_SCHEDULERS: {}})
    hass.data[DOMAIN][DATA_SCHEDULERS][entry.entry_id] = scheduler

    _register_device(hass, entry, scheduler.entity_id)
    await scheduler.async_reload()
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload an Air Scheduler config entry."""
    scheduler = hass.data[DOMAIN][DATA_SCHEDULERS].pop(entry.entry_id, None)
    if scheduler:
        scheduler.async_unload()
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply updated options from the integration configure UI."""
    scheduler = hass.data[DOMAIN][DATA_SCHEDULERS].get(entry.entry_id)
    if scheduler:
        await scheduler.async_set_config(entry.options or entry.data or DEFAULT_CONFIG)


def _register_device(hass: HomeAssistant, entry: ConfigEntry, entity_id: str) -> None:
    """Register one Air Scheduler device for the managed thermostat."""
    state = hass.states.get(entity_id)
    name = entry.title
    if state:
        name = state.attributes.get("friendly_name", entity_id)

    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entity_id)},
        manufacturer=NAME,
        model="Scheduled thermostat",
        name=name,
    )


def _all_schedulers(hass: HomeAssistant) -> list[AirScheduler]:
    """Return all active scheduler instances."""
    return list(hass.data.get(DOMAIN, {}).get(DATA_SCHEDULERS, {}).values())


def _matching_schedulers(
    hass: HomeAssistant,
    entity_ids: list[str] | str | None,
) -> list[AirScheduler]:
    """Return schedulers matching an optional entity target."""
    schedulers = _all_schedulers(hass)
    if not entity_ids:
        return schedulers
    if isinstance(entity_ids, str):
        targets = {entity_ids}
    else:
        targets = set(entity_ids)
    return [scheduler for scheduler in schedulers if scheduler.entity_id in targets]


class AirScheduler:
    """Schedule climate profile changes for one thermostat."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        config: Mapping[str, Any],
    ) -> None:
        """Initialize the scheduler."""
        self.hass = hass
        self.entry = entry
        self._config = _normalized_config(config)
        self._unsub_timer: Callable[[], None] | None = None

    @property
    def entity_id(self) -> str:
        """Return the managed climate entity id."""
        return self._config.get(ATTR_ENTITY_ID, "")

    async def async_reload(self) -> None:
        """Reload and schedule this thermostat."""
        self._schedule_next()
        if self._config.get(CONF_APPLY_ON_START, True):
            await self.async_apply_current()

    async def async_set_config(self, config: Mapping[str, Any]) -> None:
        """Activate an updated scheduler config."""
        current_entity_id = self.entity_id
        self._config = _normalized_config(config)
        self._config.setdefault(ATTR_ENTITY_ID, current_entity_id)
        self._schedule_next()

    def async_unload(self) -> None:
        """Unload this scheduler."""
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None

    async def async_apply_current(self) -> None:
        """Apply the most recent scheduled profile for this thermostat."""
        now = dt_util.now()
        latest: tuple[datetime, str] | None = None

        for schedule in self._enabled_schedules():
            last_fire = self._last_fire_before(schedule, now)
            if last_fire is None:
                continue
            if latest is None or last_fire > latest[0]:
                latest = (last_fire, schedule[CONF_PROFILE])

        if latest:
            await self.async_apply_profile(latest[1])

    async def async_apply_profile(self, profile: str) -> None:
        """Apply a named profile to this thermostat."""
        settings = self._profile_settings(profile)
        if settings is None:
            _LOGGER.warning("Unknown Air Scheduler profile: %s", profile)
            return
        if not settings:
            _LOGGER.warning(
                "Profile %s has no settings for %s",
                profile,
                self.entity_id,
            )
            return
        await self._async_apply_climate_settings(settings)

    async def _async_apply_climate_settings(self, settings: dict[str, Any]) -> None:
        """Call climate services for this thermostat."""
        hvac_mode = settings.get(CONF_HVAC_MODE)
        if hvac_mode:
            await self.hass.services.async_call(
                "climate",
                "set_hvac_mode",
                {
                    ATTR_ENTITY_ID: self.entity_id,
                    CONF_HVAC_MODE: hvac_mode,
                },
                blocking=False,
            )
            if hvac_mode == "off":
                return

        temperature_data = {
            key: settings[key]
            for key in ("temperature", "target_temp_low", "target_temp_high")
            if key in settings
        }
        if temperature_data:
            temperature_data[ATTR_ENTITY_ID] = self.entity_id
            await self.hass.services.async_call(
                "climate",
                "set_temperature",
                temperature_data,
                blocking=False,
            )

        for service, setting_key in (
            ("set_preset_mode", "preset_mode"),
            ("set_fan_mode", "fan_mode"),
            ("set_humidity", "humidity"),
        ):
            if setting_key not in settings:
                continue
            await self.hass.services.async_call(
                "climate",
                service,
                {
                    ATTR_ENTITY_ID: self.entity_id,
                    setting_key: settings[setting_key],
                },
                blocking=False,
            )

    async def _async_fire(self, now: datetime) -> None:
        """Apply schedules matching this fire time."""
        for schedule in self._enabled_schedules():
            if self._schedule_matches(schedule, now):
                await self.async_apply_profile(schedule[CONF_PROFILE])
        self._schedule_next(now + timedelta(seconds=1))

    def _schedule_next(self, after: datetime | None = None) -> None:
        """Schedule the next matching profile change."""
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None

        next_fire = self._next_fire_after(after or dt_util.now())
        if next_fire is None:
            return

        self._unsub_timer = async_track_point_in_time(
            self.hass,
            self._async_fire,
            next_fire,
        )
        _LOGGER.debug("Next Air Scheduler fire time for %s: %s", self.entity_id, next_fire)

    def _next_fire_after(self, after: datetime) -> datetime | None:
        """Return the next schedule datetime after the provided local time."""
        candidates = [
            fire_time
            for schedule in self._enabled_schedules()
            for fire_time in self._fire_times_between(
                schedule,
                after + timedelta(seconds=1),
                after + timedelta(days=8),
            )
        ]
        return min(candidates) if candidates else None

    def _last_fire_before(
        self,
        schedule: dict[str, Any],
        before: datetime,
    ) -> datetime | None:
        """Return the latest schedule datetime before the provided local time."""
        candidates = list(
            self._fire_times_between(
                schedule,
                before - timedelta(days=8),
                before,
            )
        )
        return max(candidates) if candidates else None

    def _fire_times_between(
        self,
        schedule: dict[str, Any],
        start: datetime,
        end: datetime,
    ) -> list[datetime]:
        """Return fire times for one schedule inside a datetime window."""
        schedule_time = self._parse_time(schedule[CONF_TIME])
        days = self._schedule_days(schedule)
        fire_times: list[datetime] = []
        cursor = start.date()

        while cursor <= end.date():
            candidate = datetime.combine(
                cursor,
                schedule_time,
                tzinfo=dt_util.DEFAULT_TIME_ZONE,
            )
            if start <= candidate <= end and candidate.weekday() in days:
                fire_times.append(candidate)
            cursor += timedelta(days=1)

        return fire_times

    def _schedule_matches(self, schedule: dict[str, Any], when: datetime) -> bool:
        """Return whether a schedule should fire at the provided datetime."""
        schedule_time = self._parse_time(schedule[CONF_TIME])
        return (
            when.weekday() in self._schedule_days(schedule)
            and when.hour == schedule_time.hour
            and when.minute == schedule_time.minute
            and when.second == schedule_time.second
        )

    def _enabled_schedules(self) -> list[dict[str, Any]]:
        """Return enabled, minimally valid schedules."""
        schedules = self._config.get(CONF_SCHEDULES, [])
        if not isinstance(schedules, list):
            return []

        valid_schedules = []
        for schedule in schedules:
            if not isinstance(schedule, dict) or not schedule.get(CONF_ENABLED, True):
                continue
            if CONF_PROFILE not in schedule or CONF_TIME not in schedule:
                _LOGGER.warning("Skipping invalid Air Scheduler entry: %s", schedule)
                continue
            try:
                self._parse_time(schedule[CONF_TIME])
            except ValueError as err:
                _LOGGER.warning("Skipping Air Scheduler entry: %s", err)
                continue
            valid_schedules.append(schedule)
        return valid_schedules

    def _profile_settings(self, profile: str) -> dict[str, Any] | None:
        """Return settings for one profile."""
        profile_settings = self._config.get(CONF_PROFILES, {}).get(profile)
        if profile_settings is None:
            return None
        if not isinstance(profile_settings, dict):
            return {}
        if self.entity_id in profile_settings:
            nested_settings = profile_settings[self.entity_id]
            return nested_settings if isinstance(nested_settings, dict) else {}
        return profile_settings

    def _schedule_days(self, schedule: dict[str, Any]) -> set[int]:
        """Return active weekdays for a schedule."""
        raw_days = schedule.get(CONF_DAYS)
        if not raw_days:
            return set(range(7))

        days = set()
        for raw_day in raw_days:
            day = DAY_ALIASES.get(str(raw_day).lower())
            if day is None:
                _LOGGER.warning("Ignoring unknown Air Scheduler day: %s", raw_day)
                continue
            days.add(day)
        return days or set(range(7))

    @staticmethod
    def _parse_time(value: str) -> time:
        """Parse HH:MM or HH:MM:SS."""
        parts = [int(part) for part in str(value).split(":")]
        if len(parts) == 2:
            hour, minute = parts
            second = 0
        elif len(parts) == 3:
            hour, minute, second = parts
        else:
            raise ValueError(f"Invalid schedule time: {value}")
        return time(hour, minute, second)
