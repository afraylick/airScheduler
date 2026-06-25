"""Config and options flow for Air Scheduler."""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.selector import selector

from .const import (
    CONF_APPLY_ON_START,
    CONF_DAYS,
    CONF_ENABLED,
    CONF_ENTITIES,
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
    NAME,
)

PROFILES = ["home", "away", "sleep"]
DAY_OPTIONS = [
    {"value": "mon", "label": "Monday"},
    {"value": "tue", "label": "Tuesday"},
    {"value": "wed", "label": "Wednesday"},
    {"value": "thu", "label": "Thursday"},
    {"value": "fri", "label": "Friday"},
    {"value": "sat", "label": "Saturday"},
    {"value": "sun", "label": "Sunday"},
]
HVAC_MODE_OPTIONS = [
    {"value": "unchanged", "label": "Do not change"},
    {"value": "off", "label": "Off"},
    {"value": "heat", "label": "Heat"},
    {"value": "cool", "label": "Cool"},
    {"value": "heat_cool", "label": "Heat/Cool"},
    {"value": "auto", "label": "Auto"},
    {"value": "dry", "label": "Dry"},
    {"value": "fan_only", "label": "Fan only"},
]


def _empty_config() -> dict[str, Any]:
    """Return a fresh empty scheduler config."""
    return {
        CONF_APPLY_ON_START: True,
        CONF_ENTITIES: [],
        CONF_PROFILES: {profile: {} for profile in PROFILES},
        CONF_SCHEDULES: [],
    }


def _normalize_config(config: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize config/options into the expected shape."""
    normalized = _empty_config()
    normalized.update(deepcopy(config or {}))
    normalized[CONF_ENTITIES] = list(normalized.get(CONF_ENTITIES) or [])
    normalized[CONF_PROFILES] = normalized.get(CONF_PROFILES) or {}
    for profile in PROFILES:
        normalized[CONF_PROFILES].setdefault(profile, {})
    normalized[CONF_SCHEDULES] = list(normalized.get(CONF_SCHEDULES) or [])
    return normalized


def _entity_selector(multiple: bool = True):
    """Return a climate entity selector."""
    return selector({"entity": {"domain": "climate", "multiple": multiple}})


def _profile_selector():
    """Return a profile selector."""
    return selector(
        {
            "select": {
                "options": [
                    {"value": "home", "label": "Home"},
                    {"value": "away", "label": "Away"},
                    {"value": "sleep", "label": "Sleep"},
                ]
            }
        }
    )


def _configured_entity_selector(entities: list[str], multiple: bool = False):
    """Return a selector limited to configured thermostat entities."""
    return selector(
        {
            "select": {
                "mode": "dropdown",
                "multiple": multiple,
                "options": [{"value": entity_id, "label": entity_id} for entity_id in entities],
            }
        }
    )


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

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Create the options flow."""
        return AirSchedulerOptionsFlow()

    async def async_step_user(self, user_input=None):
        """Create one Air Scheduler instance."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            config = _empty_config()
            config[CONF_ENTITIES] = user_input[CONF_ENTITIES]
            config[CONF_APPLY_ON_START] = user_input[CONF_APPLY_ON_START]
            return self.async_create_entry(title=NAME, data={}, options=config)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ENTITIES): _entity_selector(),
                    vol.Required(CONF_APPLY_ON_START, default=True): bool,
                }
            ),
        )


class AirSchedulerOptionsFlow(config_entries.OptionsFlow):
    """Handle Air Scheduler options."""

    def __init__(self) -> None:
        """Initialize options flow state."""
        self._config: dict[str, Any] = {}
        self._selected_profile: str | None = None
        self._selected_entity: str | None = None

    def _load_config(self) -> dict[str, Any]:
        """Load the current editable config."""
        if not self._config:
            self._config = _normalize_config(self.config_entry.options)
        return self._config

    async def async_step_init(self, user_input=None):
        """Show the main configure menu."""
        self._load_config()
        return self.async_show_menu(
            step_id="init",
            menu_options=["thermostats", "state_settings", "schedule_times"],
        )

    async def async_step_thermostats(self, user_input=None):
        """Configure the thermostat list and restart behavior."""
        config = self._load_config()

        if user_input is not None:
            config[CONF_ENTITIES] = user_input[CONF_ENTITIES]
            config[CONF_APPLY_ON_START] = user_input[CONF_APPLY_ON_START]
            self._remove_unconfigured_entity_settings()
            return self.async_create_entry(title="", data=config)

        return self.async_show_form(
            step_id="thermostats",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_ENTITIES,
                        default=config.get(CONF_ENTITIES, []),
                    ): _entity_selector(),
                    vol.Required(
                        CONF_APPLY_ON_START,
                        default=config.get(CONF_APPLY_ON_START, True),
                    ): bool,
                }
            ),
        )

    async def async_step_state_settings(self, user_input=None):
        """Choose which state/entity settings to edit."""
        config = self._load_config()
        entities = config.get(CONF_ENTITIES, [])
        errors = {}

        if not entities:
            errors["base"] = "no_thermostats"

        if user_input is not None and not errors:
            self._selected_profile = user_input[CONF_PROFILE]
            self._selected_entity = user_input[CONF_ENTITIES]
            return await self.async_step_edit_state()

        schema = {}
        if entities:
            schema = {
                vol.Required(CONF_PROFILE, default="home"): _profile_selector(),
                vol.Required(CONF_ENTITIES): _configured_entity_selector(entities),
            }

        return self.async_show_form(
            step_id="state_settings",
            data_schema=vol.Schema(schema),
            errors=errors,
        )

    async def async_step_edit_state(self, user_input=None):
        """Edit climate settings for one profile/entity pair."""
        config = self._load_config()
        profile = self._selected_profile or "home"
        entity_id = self._selected_entity or ""
        existing = (
            config.get(CONF_PROFILES, {})
            .get(profile, {})
            .get(entity_id, {})
        )

        if user_input is not None:
            settings = self._clean_state_settings(user_input)
            config[CONF_PROFILES].setdefault(profile, {})[entity_id] = settings
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
                CONF_PROFILE: profile.title(),
                CONF_ENTITIES: entity_id,
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
        entities = config.get(CONF_ENTITIES, [])
        errors = {}

        if not entities:
            errors["base"] = "no_thermostats"

        if user_input is not None and not errors:
            schedule = {
                "id": self._unique_schedule_id(user_input),
                CONF_NAME: user_input.get(CONF_NAME) or self._schedule_label(user_input),
                CONF_PROFILE: user_input[CONF_PROFILE],
                CONF_TIME: user_input[CONF_TIME],
                CONF_ENTITIES: user_input[CONF_ENTITIES],
                CONF_ENABLED: user_input[CONF_ENABLED],
            }
            if user_input.get(CONF_DAYS):
                schedule[CONF_DAYS] = user_input[CONF_DAYS]

            config[CONF_SCHEDULES].append(schedule)
            return self.async_create_entry(title="", data=config)

        schema = {}
        if entities:
            schema = {
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
                vol.Required(CONF_ENTITIES, default=entities): _configured_entity_selector(
                    entities,
                    multiple=True,
                ),
                vol.Required(CONF_ENABLED, default=True): bool,
            }

        return self.async_show_form(
            step_id="add_schedule",
            data_schema=vol.Schema(schema),
            errors=errors,
        )

    async def async_step_remove_schedule(self, user_input=None):
        """Remove one schedule."""
        config = self._load_config()
        schedules = config.get(CONF_SCHEDULES, [])
        errors = {}

        if not schedules:
            errors["base"] = "no_schedules"

        if user_input is not None and not errors:
            selected_index = int(user_input["schedule_index"])
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
                                {
                                    "value": str(index),
                                    "label": self._schedule_label(schedule),
                                }
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

    def _remove_unconfigured_entity_settings(self) -> None:
        """Drop saved settings for entities no longer managed by the scheduler."""
        configured = set(self._config.get(CONF_ENTITIES, []))
        for profile_settings in self._config.get(CONF_PROFILES, {}).values():
            for entity_id in list(profile_settings):
                if entity_id != "default" and entity_id not in configured:
                    profile_settings.pop(entity_id)

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
