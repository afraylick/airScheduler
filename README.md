# Air Scheduler

Air Scheduler is a Home Assistant custom integration for applying named climate profiles to thermostats on a schedule.

The first version is backend-first by design:

- A custom Lovelace card is a good editor/viewer later, but it should not run the schedule because dashboard JavaScript only runs while a dashboard is open.
- A Home Assistant app/add-on is useful when you need a separate containerized service. This scheduler mainly needs to call Home Assistant climate actions, so an integration is the smaller and more reliable core.
- The integration calls Home Assistant's climate actions, including `climate.set_hvac_mode`, `climate.set_temperature`, `climate.set_preset_mode`, `climate.set_fan_mode`, and `climate.set_humidity`.

## Install

Copy `custom_components/air_scheduler` into your Home Assistant `custom_components` directory, then restart Home Assistant.

Add the integration from **Settings > Devices & services > Add integration > Air Scheduler**.

The setup flow asks for one climate entity. Add Air Scheduler again for each additional thermostat. Each configured thermostat is registered as its own Air Scheduler device in Home Assistant.

## Configure in the UI

Open **Settings > Devices & services > Air Scheduler**, then choose the thermostat entry you want to configure.

The configure screen is split into separate sections:

- **State settings**: define the climate settings for Home, Away, and Sleep for this thermostat.
- **Schedule times**: add weekday, weekend, custom, or remove times when a state is applied to this thermostat.

For each state, you can set HVAC mode, single temperature, heat/cool low and high temperatures, preset mode, fan mode, and humidity. Leave fields blank when that setting should not be changed.

Each thermostat entry has its own state settings and its own schedule list, so the bedroom thermostat can have different Home/Away/Sleep temperatures and weekday/weekend times than the downstairs thermostat.

## Configure schedules

The GUI is the preferred path. You can also use Developer Tools > Actions and call `air_scheduler.set_config` for one managed thermostat.

The key concepts are:

- `profiles`: named states such as `home`, `away`, and `sleep`.
- Each profile defines settings for one thermostat entry.
- `schedules`: weekday, weekend, or custom day/time rules that apply one profile to that thermostat.
- `apply_on_start`: when true, Home Assistant restart applies the latest scheduled profile for the thermostat.

Example service data:

```yaml
entity_id: climate.downstairs
config:
  apply_on_start: true
  entity_id: climate.downstairs
  profiles:
    home:
      hvac_mode: heat_cool
      target_temp_low: 68
      target_temp_high: 74
    away:
      hvac_mode: heat_cool
      target_temp_low: 62
      target_temp_high: 80
    sleep:
      hvac_mode: heat_cool
      target_temp_low: 65
      target_temp_high: 78
  schedules:
    - id: weekday_morning_home
      days: [mon, tue, wed, thu, fri]
      time: "06:30"
      profile: home
    - id: weekday_leave
      days: [mon, tue, wed, thu, fri]
      time: "08:15"
      profile: away
    - id: weekend_home
      days: [sat, sun]
      time: "08:00"
      profile: home
    - id: daily_sleep
      time: "22:30"
      profile: sleep
```

## Manual override

Call `air_scheduler.apply_profile` to apply a profile immediately:

```yaml
profile: sleep
entity_id:
  - climate.downstairs
  - climate.upstairs
```

## Upgrade from v0.1.x

Version 0.2.0 changes the configuration model from one entry with many thermostats to one entry per thermostat. After upgrading, remove the old Air Scheduler entry and add Air Scheduler once for each thermostat.

## Next steps

The natural next layer is a Lovelace card that reads/writes this same config through integration services. That card should be treated as the editor, not the scheduler engine.
