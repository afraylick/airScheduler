# Air Scheduler

Air Scheduler is a Home Assistant custom integration for applying Home, Away, and Sleep climate profiles to multiple thermostats on a schedule.

The scheduler runs in the backend, so schedules keep working even when no dashboard is open. The editor is a custom Home Assistant sidebar panel instead of the integration Configure dialog.

## Install

Install with HACS as a custom integration, then restart Home Assistant.

Add the integration from **Settings > Devices & services > Add integration > Air Scheduler**.

After setup, open **Air Scheduler** from the Home Assistant sidebar.

## Configure in the Panel

The Air Scheduler panel has three working areas:

- **Thermostats**: choose the managed `climate.*` entities.
- **State settings**: define Home, Away, and Sleep settings per thermostat.
- **Schedule**: add weekday, weekend, or custom schedules.

Home, Away, and Sleep are global state names, but each state can have different settings for each thermostat. For example, Bedroom Home can be 68-74 while Downstairs Home can be 66-75.

HVAC mode is selected from a dropdown. Temperature fields that do not apply to the selected HVAC mode are disabled, shaded, and are not saved. Fan mode uses suggestions from Home Assistant when available, but also accepts custom values.

Compatibility notes:

- Frigidaire AC units from `bm1549/home-assistant-frigidaire` expose `off`, `cool`, `auto`, `fan_only`, and `dry`, with single target temperature and fan modes. Use `Temp` rather than `Low`/`High`.
- Ecobee through HomeKit can expose `heat`, `cool`, `heat_cool`, and `off` depending on HomeKit characteristics. Use `Temp` for `heat`/`cool`, and `Low`/`High` for `heat_cool`.

Schedules choose:

- which profile to apply,
- what time to apply it,
- which days it runs,
- which thermostats it targets.

## Config Shape

The panel stores a config like this:

```json
{
  "apply_on_start": true,
  "entities": ["climate.bedroom", "climate.downstairs"],
  "profiles": {
    "home": {
      "climate.bedroom": {
        "hvac_mode": "heat_cool",
        "target_temp_low": 68,
        "target_temp_high": 74
      },
      "climate.downstairs": {
        "hvac_mode": "heat_cool",
        "target_temp_low": 66,
        "target_temp_high": 75
      }
    },
    "away": {
      "climate.bedroom": {
        "hvac_mode": "heat_cool",
        "target_temp_low": 62,
        "target_temp_high": 80
      },
      "climate.downstairs": {
        "hvac_mode": "heat_cool",
        "target_temp_low": 60,
        "target_temp_high": 82
      }
    },
    "sleep": {
      "climate.bedroom": {
        "hvac_mode": "heat_cool",
        "target_temp_low": 64,
        "target_temp_high": 76
      },
      "climate.downstairs": {
        "hvac_mode": "heat_cool",
        "target_temp_low": 65,
        "target_temp_high": 78
      }
    }
  },
  "schedules": [
    {
      "id": "weekday_morning_home",
      "name": "Weekday morning",
      "enabled": true,
      "days": ["mon", "tue", "wed", "thu", "fri"],
      "time": "06:30",
      "profile": "home",
      "entities": ["climate.bedroom", "climate.downstairs"]
    },
    {
      "id": "weekend_morning_home",
      "name": "Weekend morning",
      "enabled": true,
      "days": ["sat", "sun"],
      "time": "08:00",
      "profile": "home",
      "entities": ["climate.bedroom"]
    }
  ]
}
```

## Manual Override

Call `air_scheduler.apply_profile` to apply a profile immediately:

```yaml
profile: sleep
entity_id:
  - climate.bedroom
  - climate.downstairs
```

## Upgrade Notes

Version 0.3.0 replaces the integration Configure-menu editor with the Air Scheduler sidebar panel and restores one global schedule config with per-thermostat settings.
