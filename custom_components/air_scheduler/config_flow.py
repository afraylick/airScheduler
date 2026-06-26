"""Config flow for Air Scheduler."""

from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries

from .const import DOMAIN, NAME


class AirSchedulerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle an Air Scheduler config flow."""

    VERSION = 3

    async def async_step_user(self, user_input=None):
        """Create one Air Scheduler instance."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            return self.async_create_entry(title=NAME, data={})

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({}),
        )
