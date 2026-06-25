"""Air Scheduler integration."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import datetime, time, timedelta
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.storage import Store
import homeassistant.util.dt as dt_util

from .const import (
    CONF_APPLY_ON_START,
    CONF_CONFIG,
    CONF_DAYS,
    CONF_ENABLED,
    CONF_ENTITIES,
    CONF_HVAC_MODE,
    CONF_PROFILE,
    CONF_PROFILES,
    CONF_SCHEDULES,
    CONF_TIME,
    DAY_ALIASES,
    DOMAIN,
    SERVICE_APPLY_PROFILE,
    SERVICE_RELOAD,
    SERVICE_SET_CONFIG,
    STORE_KEY,
    STORE_VERSION,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_CONFIG: dict[str, Any] = {
    CONF_APPLY_ON_START: True,
    CONF_ENTITIES: [],
    CONF_PROFILES: {
        "home": {},
        "away": {},
        "sleep": {},
    },
    CONF_SCHEDULES: [],
}

SET_CONFIG_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CONFIG): dict,
    }
)

APPLY_PROFILE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PROFILE): cv.string,
        vol.Optional(ATTR_ENTITY_ID): cv.entity_ids,
    }
)


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up Air Scheduler."""
    store = Store(hass, STORE_VERSION, STORE_KEY)
    scheduler = AirScheduler(hass, store)
    hass.data[DOMAIN] = scheduler

    async def async_set_config(call: ServiceCall) -> None:
        await scheduler.async_set_config(call.data[CONF_CONFIG])

    async def async_apply_profile(call: ServiceCall) -> None:
        await scheduler.async_apply_profile(
            call.data[CONF_PROFILE],
            call.data.get(ATTR_ENTITY_ID),
        )

    async def async_reload(call: ServiceCall) -> None:
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

    yaml_config = config.get(DOMAIN)
    if yaml_config:
        await scheduler.async_set_config(yaml_config)
    else:
        await scheduler.async_reload()

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Air Scheduler from a config entry."""
    scheduler: AirScheduler = hass.data[DOMAIN]
    await scheduler.async_set_config(entry.options or entry.data or DEFAULT_CONFIG)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload an Air Scheduler config entry."""
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply updated options from the integration configure UI."""
    scheduler: AirScheduler = hass.data[DOMAIN]
    await scheduler.async_set_config(entry.options or entry.data or DEFAULT_CONFIG)


class AirScheduler:
    """Schedule climate profile changes."""

    def __init__(self, hass: HomeAssistant, store: Store) -> None:
        """Initialize the scheduler."""
        self.hass = hass
        self._store = store
        self._config: dict[str, Any] = DEFAULT_CONFIG.copy()
        self._unsub_timer: Callable[[], None] | None = None

    async def async_reload(self) -> None:
        """Reload configuration from storage."""
        data = await self._store.async_load()
        self._config = self._normalized_config(data or DEFAULT_CONFIG)
        self._schedule_next()
        if self._config.get("apply_on_start", True):
            await self.async_apply_current()

    async def async_set_config(self, config: dict[str, Any]) -> None:
        """Persist and activate a full scheduler config."""
        self._config = self._normalized_config(config)
        await self._store.async_save(self._config)
        self._schedule_next()

    async def async_apply_current(self) -> None:
        """Apply the most recent scheduled profile for each climate entity."""
        now = dt_util.now()
        latest_by_entity: dict[str, dict[str, Any]] = {}

        for schedule in self._enabled_schedules():
            last_fire = self._last_fire_before(schedule, now)
            if last_fire is None:
                continue

            entities = self._schedule_entities(schedule) or self._profile_entities(
                schedule[CONF_PROFILE]
            )
            for entity_id in entities:
                current = latest_by_entity.get(entity_id)
                if current is None or last_fire > current["last_fire"]:
                    latest_by_entity[entity_id] = {
                        "last_fire": last_fire,
                        "profile": schedule[CONF_PROFILE],
                    }

        for entity_id, item in latest_by_entity.items():
            await self.async_apply_profile(item["profile"], [entity_id])

    async def async_apply_profile(
        self,
        profile: str,
        entity_ids: list[str] | None = None,
    ) -> None:
        """Apply a named profile to the selected climate entities."""
        profiles = self._config.get(CONF_PROFILES, {})
        profile_settings = profiles.get(profile)
        if not isinstance(profile_settings, dict):
            _LOGGER.warning("Unknown Air Scheduler profile: %s", profile)
            return

        targets = entity_ids or [
            entity_id for entity_id in profile_settings if entity_id != "default"
        ]
        for entity_id in targets:
            settings = profile_settings.get(entity_id) or profile_settings.get("default")
            if not isinstance(settings, dict):
                _LOGGER.warning(
                    "Profile %s has no settings for %s and no default settings",
                    profile,
                    entity_id,
                )
                continue
            await self._async_apply_climate_settings(entity_id, settings)

    async def _async_apply_climate_settings(
        self,
        entity_id: str,
        settings: dict[str, Any],
    ) -> None:
        """Call climate services for one entity."""
        hvac_mode = settings.get(CONF_HVAC_MODE)
        if hvac_mode:
            await self.hass.services.async_call(
                "climate",
                "set_hvac_mode",
                {
                    ATTR_ENTITY_ID: entity_id,
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
            temperature_data[ATTR_ENTITY_ID] = entity_id
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
                    ATTR_ENTITY_ID: entity_id,
                    setting_key: settings[setting_key],
                },
                blocking=False,
            )

    async def _async_fire(self, now: datetime) -> None:
        """Apply schedules matching this fire time."""
        for schedule in self._enabled_schedules():
            if self._schedule_matches(schedule, now):
                await self.async_apply_profile(
                    schedule[CONF_PROFILE],
                    self._schedule_entities(schedule),
                )
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
        _LOGGER.debug("Next Air Scheduler fire time: %s", next_fire)

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

    def _schedule_entities(self, schedule: dict[str, Any]) -> list[str] | None:
        """Return entities targeted by a schedule."""
        entities = schedule.get(CONF_ENTITIES)
        if entities is None:
            return None
        if isinstance(entities, str):
            return [entities]
        return list(entities)

    def _profile_entities(self, profile: str) -> list[str]:
        """Return entities with explicit settings in a profile."""
        profile_settings = self._config.get(CONF_PROFILES, {}).get(profile)
        if not isinstance(profile_settings, dict):
            return []
        return [entity_id for entity_id in profile_settings if entity_id != "default"]

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

    @staticmethod
    def _normalized_config(config: dict[str, Any]) -> dict[str, Any]:
        """Normalize the persisted config shape."""
        normalized = deepcopy(DEFAULT_CONFIG)
        normalized.update(deepcopy(config or {}))
        normalized[CONF_ENTITIES] = list(normalized.get(CONF_ENTITIES) or [])
        normalized[CONF_PROFILES] = normalized.get(CONF_PROFILES) or {}
        for profile in ("home", "away", "sleep"):
            normalized[CONF_PROFILES].setdefault(profile, {})
        normalized[CONF_SCHEDULES] = normalized.get(CONF_SCHEDULES) or []
        return normalized
