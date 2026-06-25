# Air Scheduler

Air Scheduler is a Home Assistant custom integration for applying named climate profiles to multiple thermostats on a schedule.

The first version is backend-first by design:

- A custom Lovelace card is a good editor/viewer later, but it should not run the schedule because dashboard JavaScript only runs while a dashboard is open.
- A Home Assistant app/add-on is useful when you need a separate containerized service. This scheduler mainly needs to call Home Assistant climate actions, so an integration is the smaller and more reliable core.
- The integration calls Home Assistant's climate actions, including `climate.set_hvac_mode`, `climate.set_temperature`, `climate.set_preset_mode`, `climate.set_fan_mode`, and `climate.set_humidity`.

## Install

Copy `custom_components/air_scheduler` into your Home Assistant `custom_components` directory, then restart Home Assistant.

Add the integration from **Settings > Devices & services > Add integration > Air Scheduler**.

The initial setup asks which climate entities Air Scheduler should manage.

## Configure in the UI

Open **Settings > Devices & services > Air Scheduler > Configure**.

The configure screen is split into separate sections:

- **Thermostats**: choose the climate entities to manage.
- **State settings**: define the climate settings for Home, Away, and Sleep one thermostat at a time.
- **Schedule times**: add or remove times when a state is applied.

For each state/thermostat pair, you can set HVAC mode, single temperature, heat/cool low and high temperatures, preset mode, fan mode, and humidity. Leave fields blank when that setting should not be changed.

## Configure schedules

The GUI is the preferred path. You can also use Developer Tools > Actions and call `air_scheduler.set_config` with a config object shaped like [examples/schedule.json](examples/schedule.json).

The key concepts are:

- `profiles`: named states such as `home`, `away`, and `sleep`.
- Each profile can define per-entity settings and/or a `default` fallback.
- `schedules`: weekday/time rules that apply one profile to selected climate entities.
- `apply_on_start`: when true, Home Assistant restart applies the latest scheduled profile for each entity.

Example service data:

```yaml
config:
  apply_on_start: true
  profiles:
    home:
      climate.downstairs:
        hvac_mode: heat_cool
        target_temp_low: 68
        target_temp_high: 74
      climate.upstairs:
        hvac_mode: heat_cool
        target_temp_low: 67
        target_temp_high: 75
    away:
      default:
        hvac_mode: heat_cool
        target_temp_low: 62
        target_temp_high: 80
    sleep:
      climate.downstairs:
        hvac_mode: heat_cool
        target_temp_low: 65
        target_temp_high: 78
      climate.upstairs:
        hvac_mode: heat_cool
        target_temp_low: 64
        target_temp_high: 76
  schedules:
    - id: weekday_morning_home
      days: [mon, tue, wed, thu, fri]
      time: "06:30"
      profile: home
      entities:
        - climate.downstairs
        - climate.upstairs
    - id: weekday_leave
      days: [mon, tue, wed, thu, fri]
      time: "08:15"
      profile: away
      entities:
        - climate.downstairs
        - climate.upstairs
    - id: daily_sleep
      time: "22:30"
      profile: sleep
      entities:
        - climate.downstairs
        - climate.upstairs
```

## Manual override

Call `air_scheduler.apply_profile` to apply a profile immediately:

```yaml
profile: sleep
entity_id:
  - climate.downstairs
  - climate.upstairs
```

## Next steps

The natural next layer is a Lovelace card that reads/writes this same config through integration services. That card should be treated as the editor, not the scheduler engine.
