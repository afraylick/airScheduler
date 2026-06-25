"""Constants for Air Scheduler."""

DOMAIN = "air_scheduler"
NAME = "Air Scheduler"

CONF_CONFIG = "config"
CONF_APPLY_ON_START = "apply_on_start"
CONF_DAYS = "days"
CONF_ENABLED = "enabled"
CONF_ENTITIES = "entities"
CONF_HVAC_MODE = "hvac_mode"
CONF_NAME = "name"
CONF_PRESET_MODE = "preset_mode"
CONF_PROFILE = "profile"
CONF_PROFILES = "profiles"
CONF_SCHEDULES = "schedules"
CONF_TARGET_TEMP_HIGH = "target_temp_high"
CONF_TARGET_TEMP_LOW = "target_temp_low"
CONF_TEMPERATURE = "temperature"
CONF_TIME = "time"

SERVICE_APPLY_PROFILE = "apply_profile"
SERVICE_RELOAD = "reload"
SERVICE_SET_CONFIG = "set_config"

STORE_KEY = DOMAIN
STORE_VERSION = 1

DAY_ALIASES = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "wed": 2,
    "wednesday": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}
