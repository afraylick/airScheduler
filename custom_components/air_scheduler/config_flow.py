"""Config and options flow for Air Scheduler."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import callback
from homeassistant.helpers.selector import selector

from .const import (
    CONF_APPLY_ON_START,
    CONF_DAYS,
    CONF_ENABLED,
    CONF_HVAC_MODE,
    CONF_NAME,
    CONF_PRESET_MODE,
    CONF_PROFILE,
    CONF_PROFILES,
    CONF_SCHEDULES,
    CONF_TARGET_TEMP_HIGH,
    CONF_TARGET_TEMP_LOW,
    CONF_TEMPERATURE,
    CONF_TIME,
    DOMAIN,
)

PROFILES = ["home", "away", "sleep"]
DAY_OPTIONS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
HVAC_MODE_OPTIONS = [
    "unchanged",
    "off",
    "heat",
    "cool",
    "heat_cool",
    "auto",
    "dry",
    "fan_only",
]


def _empty_config(entity_id: str | None = None) -> dict[str, Any]:
    """Return a fresh scheduler config for one thermostat."""
    config = {
        CONF_APPLY_ON_START: True,
        CONF_PROFILES: {profile: {} for profile in PROFILES},
        CONF_SCHEDULES: [],
    }
    if entity_id:
        config[ATTR_ENTITY_ID] = entity_id
    return config


def _plain_data(value: Any) -> Any:
    """Convert Home Assistant read-only config data into plain containers."""
    if isinstance(value, Mapping):
        return {key: _plain_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain_data(item) for item in value]
    if isinstance(value, tuple):
        return [_plain_data(item) for item in value]
    return value


def _normalize_config(config: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalize config/options into the expected per-thermostat shape."""
    source = _plain_data(config or {})
    entity_id = source.get(ATTR_ENTITY_ID)
    if not entity_id and source.get("entities"):
        entity_id = source["entities"][0]

    normalized = _empty_config(entity_id)
    normalized.update(source)
    normalized.pop("entities", None)
    normalized[CONF_PROFILES] = normalized.get(CONF_PROFILES) or {}
    for profile in PROFILES:
        settings = normalized[CONF_PROFILES].get(profile, {})
        if entity_id and isinstance(settings, dict) and entity_id in settings:
            settings = settings[entity_id]
        elif isinstance(settings, dict) and "default" in settings:
            settings = settings["default"]
        normalized[CONF_PROFILES][profile] = settings if isinstance(settings, dict) else {}
    normalized[CONF_SCHEDULES] = list(normalized.get(CONF_SCHEDULES) or [])
    return normalized


def _entity_selector():
    """Return a single climate entity selector."""
    return selector({"entity": {"domain": "climate"}})


def _profile_selector():
    """Return a profile selector."""
    return selector({"select": {"options": PROFILES}})


def _number_selector(minimum: int, maximum: int):
    """Return a temperature/humidity number selector."""
    return selector(
        {
            "number": {
                "min": minimum,
                "max": maximum,
                "step": 1,
                "mode": "box",
            }
        }
    )


def _optional_with_default(key: str, existing: dict[str, Any]):
    """Return an optional voluptuous key, preserving an existing default."""
    if key in existing:
        return vol.Optional(key, default=existing[key])
    return vol.Optional(key)


class AirSchedulerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle an Air Scheduler config flow."""

    VERSION = 2

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Create the options flow."""
        return AirSchedulerOptionsFlow()

    async def async_step_user(self, user_input=None):
        """Create one Air Scheduler thermostat entry."""
        errors = {}

        if user_input is not None:
            entity_id = user_input[ATTR_ENTITY_ID]
            await self.async_set_unique_id(entity_id)
            self._abort_if_unique_id_configured()

            config = _empty_config(entity_id)
            config[CONF_APPLY_ON_START] = user_input[CONF_APPLY_ON_START]
            return self.async_create_entry(
                title=self._entry_title(entity_id),
                data=config,
                options=config,
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(ATTR_ENTITY_ID): _entity_selector(),
                    vol.Required(CONF_APPLY_ON_START, default=True): bool,
                }
            ),
            errors=errors,
        )

    def _entry_title(self, entity_id: str) -> str:
        """Return a friendly config entry title."""
        state = self.hass.states.get(entity_id)
        if state:
            return state.attributes.get("friendly_name", entity_id)
        return entity_id


class AirSchedulerOptionsFlow(config_entries.OptionsFlow):
    """Handle Air Scheduler options for one thermostat."""

    def __init__(self) -> None:
        """Initialize options flow state."""
        self._config: dict[str, Any] = {}
        self._selected_profile: str | None = None

    def _load_config(self) -> dict[str, Any]:
        """Load the current editable config."""
        if not self._config:
            self._config = _normalize_config(
                self.config_entry.options or self.config_entry.data
            )
        return self._config

    async def async_step_init(self, user_input=None):
        """Show the main configure menu."""
        self._load_config()
        return self.async_show_menu(
            step_id="init",
            menu_options=["state_settings", "schedule_times"],
        )

    async def async_step_state_settings(self, user_input=None):
        """Choose which state settings to edit."""
        if user_input is not None:
            self._selected_profile = user_input[CONF_PROFILE]
            return await self.async_step_edit_state()

        return self.async_show_form(
            step_id="state_settings",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PROFILE, default="home"): _profile_selector(),
                }
            ),
        )

    async def async_step_edit_state(self, user_input=None):
        """Edit climate settings for one profile."""
        config = self._load_config()
        profile = self._selected_profile or "home"
        existing = config.get(CONF_PROFILES, {}).get(profile, {})

        if user_input is not None:
            settings = self._clean_state_settings(user_input)
            config[CONF_PROFILES][profile] = settings
            return self.async_create_entry(title="", data=config)

        hvac_default = existing.get(CONF_HVAC_MODE, "unchanged")
        schema = {
            vol.Required(CONF_HVAC_MODE, default=hvac_default): selector(
                {"select": {"options": HVAC_MODE_OPTIONS}}
            ),
            _optional_with_default(CONF_TEMPERATURE, existing): _number_selector(40, 100),
            _optional_with_default(CONF_TARGET_TEMP_LOW, existing): _number_selector(40, 100),
            _optional_with_default(CONF_TARGET_TEMP_HIGH, existing): _number_selector(40, 100),
            _optional_with_default(CONF_PRESET_MODE, existing): str,
            _optional_with_default("fan_mode", existing): str,
            _optional_with_default("humidity", existing): _number_selector(0, 100),
        }

        return self.async_show_form(
            step_id="edit_state",
            data_schema=vol.Schema(schema),
            description_placeholders={
                CONF_PROFILE: profile,
                ATTR_ENTITY_ID: config.get(ATTR_ENTITY_ID, ""),
            },
        )

    async def async_step_schedule_times(self, user_input=None):
        """Show schedule actions."""
        self._load_config()
        return self.async_show_menu(
            step_id="schedule_times",
            menu_options=["add_schedule", "remove_schedule"],
        )

    async def async_step_add_schedule(self, user_input=None):
        """Add a scheduled profile application."""
        config = self._load_config()

        if user_input is not None:
            schedule = {
                "id": self._unique_schedule_id(user_input),
                CONF_NAME: user_input.get(CONF_NAME) or self._schedule_label(user_input),
                CONF_PROFILE: user_input[CONF_PROFILE],
                CONF_TIME: user_input[CONF_TIME],
                CONF_ENABLED: user_input[CONF_ENABLED],
            }
            if user_input.get(CONF_DAYS):
                schedule[CONF_DAYS] = user_input[CONF_DAYS]

            config[CONF_SCHEDULES].append(schedule)
            return self.async_create_entry(title="", data=config)

        return self.async_show_form(
            step_id="add_schedule",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_NAME): str,
                    vol.Required(CONF_PROFILE, default="home"): _profile_selector(),
                    vol.Required(CONF_TIME): selector({"time": {}}),
                    vol.Optional(CONF_DAYS, default=[]): selector(
                        {
                            "select": {
                                "multiple": True,
                                "options": DAY_OPTIONS,
                            }
                        }
                    ),
                    vol.Required(CONF_ENABLED, default=True): bool,
                }
            ),
        )

    async def async_step_remove_schedule(self, user_input=None):
        """Remove one schedule."""
        config = self._load_config()
        schedules = config.get(CONF_SCHEDULES, [])
        errors = {}

        if not schedules:
            errors["base"] = "no_schedules"

        if user_input is not None and not errors:
            selected_index = int(str(user_input["schedule_index"]).split(":", 1)[0])
            config[CONF_SCHEDULES] = [
                schedule
                for index, schedule in enumerate(schedules)
                if index != selected_index
            ]
            return self.async_create_entry(title="", data=config)

        schema = {}
        if schedules:
            schema = {
                vol.Required("schedule_index"): selector(
                    {
                        "select": {
                            "options": [
                                f"{index}: {self._schedule_label(schedule)}"
                                for index, schedule in enumerate(schedules)
                            ]
                        }
                    }
                )
            }

        return self.async_show_form(
            step_id="remove_schedule",
            data_schema=vol.Schema(schema),
            errors=errors,
        )

    @staticmethod
    def _clean_state_settings(user_input: dict[str, Any]) -> dict[str, Any]:
        """Remove empty optional settings from form input."""
        settings = {}
        for key, value in user_input.items():
            if value in (None, ""):
                continue
            if key == CONF_HVAC_MODE and value == "unchanged":
                continue
            settings[key] = value
        return settings

    def _unique_schedule_id(self, schedule: dict[str, Any]) -> str:
        """Generate a stable unique schedule id."""
        base_label = schedule.get(CONF_NAME) or self._schedule_label(schedule)
        base = re.sub(r"[^a-z0-9]+", "_", base_label.lower()).strip("_") or "schedule"
        existing_ids = {
            existing.get("id")
            for existing in self._config.get(CONF_SCHEDULES, [])
        }
        candidate = base
        counter = 2
        while candidate in existing_ids:
            candidate = f"{base}_{counter}"
            counter += 1
        return candidate

    @staticmethod
    def _schedule_label(schedule: dict[str, Any]) -> str:
        """Return a human-readable schedule label."""
        if schedule.get(CONF_NAME):
            return schedule[CONF_NAME]
        profile = str(schedule.get(CONF_PROFILE, "profile")).title()
        schedule_time = schedule.get(CONF_TIME, "time")
        return f"{profile} at {schedule_time}"
