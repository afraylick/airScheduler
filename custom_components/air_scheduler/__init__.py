"""Air Scheduler integration."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, time, timedelta
from pathlib import Path
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components import frontend, websocket_api
try:
    from homeassistant.components.http import StaticPathConfig
except ImportError:  # pragma: no cover - compatibility with older HA cores
    StaticPathConfig = None
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
    NAME,
    SERVICE_APPLY_PROFILE,
    SERVICE_RELOAD,
    SERVICE_SET_CONFIG,
    STORE_KEY,
    STORE_VERSION,
)

_LOGGER = logging.getLogger(__name__)

DATA_SCHEDULER = "scheduler"
PANEL_URL_PATH = "air-scheduler"
PROFILES = ("home", "away", "sleep")
FRONTEND_PATH = Path(__file__).parent / "frontend"
FRONTEND_URL = f"/{DOMAIN}/frontend/air-scheduler-panel.js"

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
    """Normalize stored config into the global scheduler shape."""
    source = _plain_data(config or {})
    normalized = _plain_data(DEFAULT_CONFIG)
    normalized.update(source)

    entity_id = normalized.pop(ATTR_ENTITY_ID, None)
    raw_entities = normalized.get(CONF_ENTITIES) or []
    if isinstance(raw_entities, str):
        raw_entities = [raw_entities]
    if entity_id and entity_id not in raw_entities:
        raw_entities.append(entity_id)
    normalized[CONF_ENTITIES] = list(dict.fromkeys(raw_entities))

    normalized[CONF_PROFILES] = normalized.get(CONF_PROFILES) or {}
    for profile in PROFILES:
        raw_settings = normalized[CONF_PROFILES].get(profile, {})
        normalized[CONF_PROFILES][profile] = _normalized_profile_settings(
            raw_settings,
            normalized[CONF_ENTITIES],
        )

    normalized[CONF_SCHEDULES] = [
        _normalized_schedule(schedule, normalized[CONF_ENTITIES])
        for schedule in normalized.get(CONF_SCHEDULES) or []
        if isinstance(schedule, dict)
    ]
    return normalized


def _normalized_profile_settings(
    raw_settings: Any,
    entities: list[str],
) -> dict[str, dict[str, Any]]:
    """Normalize profile settings to entity_id -> settings."""
    if not isinstance(raw_settings, dict):
        return {}

    if any(entity_id in raw_settings for entity_id in entities) or "default" in raw_settings:
        default_settings = raw_settings.get("default", {})
        return {
            entity_id: _plain_data(raw_settings.get(entity_id) or default_settings or {})
            for entity_id in entities
        }

    # v0.2 per-entry settings or service data for one thermostat.
    if len(entities) == 1:
        return {entities[0]: _plain_data(raw_settings)}

    return {entity_id: {} for entity_id in entities}


def _normalized_schedule(
    schedule: dict[str, Any],
    entities: list[str],
) -> dict[str, Any]:
    """Normalize one schedule."""
    normalized = _plain_data(schedule)
    raw_entities = normalized.get(CONF_ENTITIES)
    if raw_entities is None:
        normalized[CONF_ENTITIES] = list(entities)
    elif isinstance(raw_entities, str):
        normalized[CONF_ENTITIES] = [raw_entities]
    else:
        normalized[CONF_ENTITIES] = list(raw_entities)
    normalized.setdefault(CONF_ENABLED, True)
    return normalized


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
    hass.data[DOMAIN] = {DATA_SCHEDULER: scheduler}

    await _async_register_frontend(hass)
    _register_websocket_commands(hass)
    _register_services(hass, scheduler)
    await scheduler.async_reload()
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Air Scheduler from a config entry."""
    scheduler: AirScheduler = hass.data[DOMAIN][DATA_SCHEDULER]
    await scheduler.async_import_entry_config(entry.options or entry.data)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload an Air Scheduler config entry."""
    return True


async def _async_register_frontend(hass: HomeAssistant) -> None:
    """Register the Air Scheduler frontend panel."""
    if StaticPathConfig is not None:
        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    f"/{DOMAIN}/frontend",
                    str(FRONTEND_PATH),
                    False,
                )
            ]
        )
    else:
        hass.http.register_static_path(
            f"/{DOMAIN}/frontend",
            str(FRONTEND_PATH),
            cache_headers=False,
        )

    frontend.async_register_built_in_panel(
        hass,
        component_name="custom",
        sidebar_title=NAME,
        sidebar_icon="mdi:calendar-clock",
        frontend_url_path=PANEL_URL_PATH,
        require_admin=True,
        config={
            "_panel_custom": {
                "name": "air-scheduler-panel",
                "module_url": FRONTEND_URL,
                "embed_iframe": False,
                "trust_external_script": False,
            }
        },
    )


def _register_services(hass: HomeAssistant, scheduler: AirScheduler) -> None:
    """Register integration services."""

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


def _register_websocket_commands(hass: HomeAssistant) -> None:
    """Register websocket commands used by the custom panel."""

    @websocket_api.websocket_command(
        {
            vol.Required("type"): f"{DOMAIN}/config/get",
        }
    )
    @websocket_api.async_response
    async def websocket_get_config(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        scheduler: AirScheduler = hass.data[DOMAIN][DATA_SCHEDULER]
        connection.send_result(msg["id"], scheduler.config)

    @websocket_api.websocket_command(
        {
            vol.Required("type"): f"{DOMAIN}/config/save",
            vol.Required(CONF_CONFIG): dict,
        }
    )
    @websocket_api.require_admin
    @websocket_api.async_response
    async def websocket_save_config(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        scheduler: AirScheduler = hass.data[DOMAIN][DATA_SCHEDULER]
        await scheduler.async_set_config(msg[CONF_CONFIG])
        connection.send_result(msg["id"], scheduler.config)

    @websocket_api.websocket_command(
        {
            vol.Required("type"): f"{DOMAIN}/profile/apply",
            vol.Required(CONF_PROFILE): cv.string,
            vol.Optional(ATTR_ENTITY_ID): cv.entity_ids,
        }
    )
    @websocket_api.require_admin
    @websocket_api.async_response
    async def websocket_apply_profile(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        scheduler: AirScheduler = hass.data[DOMAIN][DATA_SCHEDULER]
        await scheduler.async_apply_profile(msg[CONF_PROFILE], msg.get(ATTR_ENTITY_ID))
        connection.send_result(msg["id"], {"applied": True})

    websocket_api.async_register_command(hass, websocket_get_config)
    websocket_api.async_register_command(hass, websocket_save_config)
    websocket_api.async_register_command(hass, websocket_apply_profile)


class AirScheduler:
    """Schedule climate profile changes."""

    def __init__(self, hass: HomeAssistant, store: Store) -> None:
        """Initialize the scheduler."""
        self.hass = hass
        self._store = store
        self._config = _plain_data(DEFAULT_CONFIG)
        self._unsub_timer: Callable[[], None] | None = None

    @property
    def config(self) -> dict[str, Any]:
        """Return the active scheduler config."""
        return _plain_data(self._config)

    async def async_reload(self) -> None:
        """Reload configuration from storage."""
        stored = await self._store.async_load()
        self._config = _normalized_config(stored or DEFAULT_CONFIG)
        self._schedule_next()
        if self._config.get(CONF_APPLY_ON_START, True):
            await self.async_apply_current()

    async def async_import_entry_config(self, config: Mapping[str, Any] | None) -> None:
        """Import old config-entry options into the global storage config."""
        if not config:
            return

        stored = await self._store.async_load()
        if not stored or not stored.get(CONF_ENTITIES):
            imported = _normalized_config(config)
            if imported.get(CONF_ENTITIES):
                await self.async_set_config(imported)
            return

        imported = _normalized_config(config)
        imported_entities = imported.get(CONF_ENTITIES, [])
        if not imported_entities:
            return

        merged = _normalized_config(stored)
        changed = False
        for entity_id in imported_entities:
            if entity_id not in merged[CONF_ENTITIES]:
                merged[CONF_ENTITIES].append(entity_id)
                changed = True
            for profile in PROFILES:
                settings = imported.get(CONF_PROFILES, {}).get(profile, {}).get(entity_id)
                if settings and not merged[CONF_PROFILES][profile].get(entity_id):
                    merged[CONF_PROFILES][profile][entity_id] = settings
                    changed = True

        existing_schedule_ids = {
            schedule.get("id")
            for schedule in merged.get(CONF_SCHEDULES, [])
            if isinstance(schedule, dict)
        }
        for schedule in imported.get(CONF_SCHEDULES, []):
            if not isinstance(schedule, dict):
                continue
            schedule_id = schedule.get("id")
            if schedule_id and schedule_id in existing_schedule_ids:
                continue
            merged[CONF_SCHEDULES].append(schedule)
            if schedule_id:
                existing_schedule_ids.add(schedule_id)
            changed = True

        if changed:
            await self.async_set_config(merged)

    async def async_set_config(self, config: Mapping[str, Any]) -> None:
        """Persist and activate scheduler config."""
        self._config = _normalized_config(config)
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

            for entity_id in self._schedule_entities(schedule):
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
        """Apply a named profile to selected climate entities."""
        profiles = self._config.get(CONF_PROFILES, {})
        profile_settings = profiles.get(profile)
        if not isinstance(profile_settings, dict):
            _LOGGER.warning("Unknown Air Scheduler profile: %s", profile)
            return

        targets = entity_ids or self._config.get(CONF_ENTITIES, [])
        for entity_id in targets:
            settings = profile_settings.get(entity_id)
            if not isinstance(settings, dict) or not settings:
                _LOGGER.warning(
                    "Profile %s has no settings for %s",
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

        temperature_data = self._temperature_data_for_mode(settings)
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

    @staticmethod
    def _temperature_data_for_mode(settings: dict[str, Any]) -> dict[str, Any]:
        """Return temperature fields valid for the configured HVAC mode."""
        hvac_mode = settings.get(CONF_HVAC_MODE)
        if hvac_mode in ("off", "fan_only"):
            return {}
        if hvac_mode == "heat_cool":
            return {
                key: settings[key]
                for key in ("target_temp_low", "target_temp_high")
                if key in settings
            }
        if hvac_mode:
            return {
                "temperature": settings["temperature"]
            } if "temperature" in settings else {}

        return {
            key: settings[key]
            for key in ("temperature", "target_temp_low", "target_temp_high")
            if key in settings
        }

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

    def _schedule_entities(self, schedule: dict[str, Any]) -> list[str]:
        """Return entities targeted by a schedule."""
        entities = schedule.get(CONF_ENTITIES)
        if entities is None:
            return list(self._config.get(CONF_ENTITIES, []))
        if isinstance(entities, str):
            return [entities]
        return list(entities)

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
