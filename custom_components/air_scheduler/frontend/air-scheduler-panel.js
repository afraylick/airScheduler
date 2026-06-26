const PROFILES = ["home", "away", "sleep"];
const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const WEEK_VIEW_DAYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"];
const DAY_LABELS = {
  sun: "Sun",
  mon: "Mon",
  tue: "Tue",
  wed: "Wed",
  thu: "Thu",
  fri: "Fri",
  sat: "Sat",
};
const FALLBACK_HVAC_MODES = ["off", "heat", "cool", "heat_cool", "auto", "dry", "fan_only"];
const FALLBACK_FAN_MODES = ["auto", "on", "circulate", "quiet", "low", "medium", "high"];
const SETTING_FIELDS = [
  ["hvac_mode", "HVAC"],
  ["temperature", "Temp"],
  ["target_temp_low", "Low"],
  ["target_temp_high", "High"],
  ["preset_mode", "Preset"],
  ["fan_mode", "Fan"],
  ["humidity", "Humidity"],
];
const NUMBER_FIELDS = ["temperature", "target_temp_low", "target_temp_high", "humidity"];
const TEMPERATURE_FIELDS = ["temperature", "target_temp_low", "target_temp_high"];

class AirSchedulerPanel extends HTMLElement {
  connectedCallback() {
    this._config = null;
    this._saving = false;
    this._loading = false;
    this._error = "";
    this._collapsedSections = new Set(["state_settings"]);
    if (this._hass) {
      this._loadConfig();
    } else {
      this._render();
    }
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._config && !this._loading) {
      this._loadConfig();
    }
  }

  async _loadConfig() {
    if (!this._hass) {
      return;
    }
    this._loading = true;
    try {
      this._config = await this._hass.callWS({ type: "air_scheduler/config/get" });
      this._normalizeConfig();
      this._render();
    } catch (err) {
      this._error = err.message || String(err);
      this._render();
    } finally {
      this._loading = false;
    }
  }

  async _saveConfig() {
    this._syncFormToConfig();
    this._saving = true;
    this._error = "";
    this._render();
    try {
      this._config = await this._hass.callWS({
        type: "air_scheduler/config/save",
        config: this._config,
      });
      this._normalizeConfig();
    } catch (err) {
      this._error = err.message || String(err);
    } finally {
      this._saving = false;
      this._render();
    }
  }

  _normalizeConfig() {
    this._config = this._config || {};
    this._config.apply_on_start = this._config.apply_on_start !== false;
    this._config.entities = Array.isArray(this._config.entities)
      ? [...new Set(this._config.entities)]
      : [];
    this._config.profiles = this._config.profiles || {};
    this._config.schedules = Array.isArray(this._config.schedules)
      ? this._config.schedules
      : [];

    for (const profile of PROFILES) {
      this._config.profiles[profile] = this._config.profiles[profile] || {};
      for (const entityId of this._config.entities) {
        this._config.profiles[profile][entityId] =
          this._config.profiles[profile][entityId] || {};
      }
    }

    const schedules = [];
    const usedScheduleIds = new Set();
    for (const schedule of this._config.schedules) {
      const targetEntities = [...new Set(
        Array.isArray(schedule.entities) && schedule.entities.length
          ? schedule.entities
          : this._config.entities
      )].filter((entityId) => this._config.entities.includes(entityId));

      for (const entityId of targetEntities) {
        const name = schedule.name || "Segment";
        const normalized = {
          ...schedule,
          id: this._uniqueScheduleId(
            targetEntities.length > 1 ? `${schedule.id || name}_${entityId}` : schedule.id || name,
            usedScheduleIds
          ),
          name,
          enabled: schedule.enabled !== false,
          profile: PROFILES.includes(schedule.profile) ? schedule.profile : "home",
          time: schedule.time || "06:30",
          days: this._normalizeDays(schedule.days),
          entities: [entityId],
        };
        usedScheduleIds.add(normalized.id);
        schedules.push(normalized);
      }
    }
    this._config.schedules = schedules;
  }

  get _climateEntities() {
    const states = this._hass?.states || {};
    return Object.keys(states)
      .filter((entityId) => entityId.startsWith("climate."))
      .sort((a, b) => this._friendlyName(a).localeCompare(this._friendlyName(b)));
  }

  _friendlyName(entityId) {
    const state = this._hass?.states?.[entityId];
    return state?.attributes?.friendly_name || entityId;
  }

  _hvacModes(entityId) {
    const modes = this._hass?.states?.[entityId]?.attributes?.hvac_modes;
    return Array.isArray(modes) && modes.length ? modes : FALLBACK_HVAC_MODES;
  }

  _fanModes(entityId) {
    const modes = this._hass?.states?.[entityId]?.attributes?.fan_modes;
    return Array.isArray(modes) && modes.length ? modes : FALLBACK_FAN_MODES;
  }

  _addEntity(entityId) {
    if (!entityId || this._config.entities.includes(entityId)) {
      return;
    }
    this._config.entities.push(entityId);
    for (const profile of PROFILES) {
      this._config.profiles[profile][entityId] = {};
    }
    this._render();
  }

  _removeEntity(entityId) {
    this._config.entities = this._config.entities.filter((item) => item !== entityId);
    for (const profile of PROFILES) {
      delete this._config.profiles[profile][entityId];
    }
    this._config.schedules = this._config.schedules.filter(
      (schedule) => !(schedule.entities || []).includes(entityId)
    );
    this._render();
  }

  _setProfileValue(profile, entityId, key, value) {
    const settings = this._config.profiles[profile][entityId];
    if (value === "") {
      delete settings[key];
    } else if (NUMBER_FIELDS.includes(key)) {
      settings[key] = Number(value);
    } else {
      settings[key] = value;
    }
    if (key === "hvac_mode") {
      this._pruneDisabledSettings(profile, entityId);
    }
  }

  _isSettingDisabled(key, hvacMode) {
    if (!TEMPERATURE_FIELDS.includes(key)) {
      return false;
    }
    if (["off", "fan_only"].includes(hvacMode)) {
      return true;
    }
    if (!hvacMode) {
      return false;
    }
    if (hvacMode === "heat_cool") {
      return key === "temperature";
    }
    return key === "target_temp_low" || key === "target_temp_high";
  }

  _pruneDisabledSettings(profile, entityId) {
    const settings = this._config.profiles[profile][entityId];
    const hvacMode = settings.hvac_mode || "";
    for (const key of TEMPERATURE_FIELDS) {
      if (this._isSettingDisabled(key, hvacMode)) {
        delete settings[key];
      }
    }
  }

  _updateTemperatureAvailability(profile, entityId) {
    const hvacMode = this._config.profiles[profile]?.[entityId]?.hvac_mode || "";
    this.querySelectorAll(`[data-profile="${profile}"][data-entity="${entityId}"][data-setting]`)
      .forEach((input) => {
        const key = input.dataset.setting;
        if (key === "hvac_mode") {
          return;
        }
        const disabled = this._isSettingDisabled(key, hvacMode);
        input.disabled = disabled;
        if (disabled) {
          input.value = "";
        }
      });
  }

  _addSchedule(entityId) {
    if (!this._config.entities.includes(entityId)) {
      return;
    }
    this._config.schedules.push({
      id: this._scheduleId(`${this._friendlyName(entityId)} Segment`),
      name: "Segment",
      enabled: true,
      profile: "home",
      time: "06:30",
      days: [...DAYS],
      entities: [entityId],
    });
    this._render();
  }

  _removeSchedule(index) {
    this._config.schedules.splice(index, 1);
    this._render();
  }

  _setScheduleValue(index, key, value) {
    const schedule = this._config.schedules[index];
    if (key === "enabled") {
      schedule.enabled = value;
    } else {
      schedule[key] = value;
    }
  }

  _toggleScheduleDay(index, day) {
    const schedule = this._config.schedules[index];
    const days = new Set(schedule.days || []);
    if (days.has(day)) {
      days.delete(day);
    } else {
      days.add(day);
    }
    schedule.days = DAYS.filter((item) => days.has(item));
    this._render();
  }

  _scheduleId(label) {
    return this._uniqueScheduleId(label, new Set(this._config.schedules.map((schedule) => schedule.id)));
  }

  _uniqueScheduleId(label, existing) {
    const base = label.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "") || "schedule";
    let candidate = base;
    let counter = 2;
    while (existing.has(candidate)) {
      candidate = `${base}_${counter}`;
      counter += 1;
    }
    return candidate;
  }

  _normalizeDays(days) {
    const selected = new Set(Array.isArray(days) ? days : DAYS);
    return DAYS.filter((day) => selected.has(day));
  }

  _scheduleEntriesForEntity(entityId) {
    return this._config.schedules
      .map((schedule, index) => ({ schedule, index }))
      .filter(({ schedule }) => (schedule.entities || []).includes(entityId));
  }

  _timeToMinutes(time) {
    const match = /^(\d{1,2}):(\d{2})$/.exec(time || "");
    if (!match) {
      return null;
    }
    const hours = Number(match[1]);
    const minutes = Number(match[2]);
    if (hours > 23 || minutes > 59) {
      return null;
    }
    return hours * 60 + minutes;
  }

  _formatMinutes(minutes) {
    const normalized = minutes % 1440;
    const hours = Math.floor(normalized / 60);
    const mins = normalized % 60;
    const suffix = hours >= 12 ? "PM" : "AM";
    const hour = hours % 12 || 12;
    return `${hour}:${String(mins).padStart(2, "0")} ${suffix}`;
  }

  _hourLabel(hour) {
    const suffix = hour >= 12 ? "PM" : "AM";
    return `${hour % 12 || 12} ${suffix}`;
  }

  _profileLabel(profile) {
    return `${profile.charAt(0).toUpperCase()}${profile.slice(1)}`;
  }

  _temperatureSummary(entityId, profile) {
    const settings = this._config.profiles?.[profile]?.[entityId] || {};
    if (settings.hvac_mode === "off") {
      return "Off";
    }
    const unit =
      this._hass?.states?.[entityId]?.attributes?.temperature_unit ||
      this._hass?.config?.unit_system?.temperature ||
      "°";
    const formatTemp = (value) => `${value}${unit}`;
    if (settings.target_temp_low !== undefined && settings.target_temp_high !== undefined) {
      return `${formatTemp(settings.target_temp_low)}-${formatTemp(settings.target_temp_high)}`;
    }
    if (settings.temperature !== undefined) {
      return formatTemp(settings.temperature);
    }
    if (settings.target_temp_low !== undefined) {
      return `Low ${formatTemp(settings.target_temp_low)}`;
    }
    if (settings.target_temp_high !== undefined) {
      return `High ${formatTemp(settings.target_temp_high)}`;
    }
    return settings.hvac_mode ? this._profileLabel(settings.hvac_mode.replace(/_/g, " ")) : "No temp";
  }

  _weekBlocksForEntity(entityId) {
    const transitions = [];
    for (const { schedule, index } of this._scheduleEntriesForEntity(entityId)) {
      if (schedule.enabled === false) {
        continue;
      }
      const minutes = this._timeToMinutes(schedule.time);
      if (minutes === null) {
        continue;
      }
      for (const day of schedule.days || []) {
        const dayIndex = WEEK_VIEW_DAYS.indexOf(day);
        if (dayIndex === -1) {
          continue;
        }
        transitions.push({
          day,
          dayIndex,
          minutes,
          absolute: dayIndex * 1440 + minutes,
          schedule,
          index,
        });
      }
    }

    const blocksByDay = new Map(WEEK_VIEW_DAYS.map((day) => [day, []]));
    if (!transitions.length) {
      return blocksByDay;
    }

    transitions.sort((a, b) => a.absolute - b.absolute || a.index - b.index);
    const findActiveTransition = (minute) => {
      let active = transitions[transitions.length - 1];
      for (const transition of transitions) {
        if (transition.absolute <= minute) {
          active = transition;
        } else {
          break;
        }
      }
      return active;
    };

    WEEK_VIEW_DAYS.forEach((day, dayIndex) => {
      const dayStart = dayIndex * 1440;
      const dayEnd = dayStart + 1440;
      const dayTransitions = transitions.filter((transition) => transition.dayIndex === dayIndex);
      const points = [...new Set([
        dayStart,
        ...dayTransitions
          .map((transition) => transition.absolute)
          .filter((minute) => minute > dayStart && minute < dayEnd),
        dayEnd,
      ])].sort((a, b) => a - b);

      for (let pointIndex = 0; pointIndex < points.length - 1; pointIndex += 1) {
        const start = points[pointIndex];
        const end = points[pointIndex + 1];
        if (end <= start) {
          continue;
        }
        const active = findActiveTransition(start);
        const profile = PROFILES.includes(active.schedule.profile) ? active.schedule.profile : "home";
        const blocks = blocksByDay.get(day);
        const previousBlock = blocks[blocks.length - 1];
        if (
          previousBlock &&
          previousBlock.index === active.index &&
          previousBlock.profile === profile &&
          previousBlock.endMinute === start - dayStart
        ) {
          previousBlock.endMinute = end - dayStart;
          previousBlock.height = ((end - dayStart - previousBlock.startMinute) / 1440) * 100;
          continue;
        }
        blocks.push({
          index: active.index,
          profile,
          temperature: this._temperatureSummary(entityId, profile),
          startMinute: start - dayStart,
          endMinute: end - dayStart,
          time: active.schedule.time || "06:30",
          top: ((start - dayStart) / 1440) * 100,
          height: ((end - start) / 1440) * 100,
        });
      }
    });

    return blocksByDay;
  }

  async _applyProfile(profile) {
    try {
      await this._hass.callWS({
        type: "air_scheduler/profile/apply",
        profile,
        entity_id: this._config.entities,
      });
    } catch (err) {
      this._error = err.message || String(err);
      this._render();
    }
  }

  _syncFormToConfig() {
    this.querySelectorAll("[data-profile][data-entity][data-setting]").forEach((input) => {
      if (input.disabled) {
        return;
      }
      this._setProfileValue(
        input.dataset.profile,
        input.dataset.entity,
        input.dataset.setting,
        input.value
      );
    });
    this.querySelectorAll("[data-schedule-field]").forEach((input) => {
      const value = input.type === "checkbox" ? input.checked : input.value;
      this._setScheduleValue(Number(input.dataset.schedule), input.dataset.scheduleField, value);
    });
    this._config.apply_on_start = Boolean(this.querySelector("#apply-on-start")?.checked);
  }

  _isCollapsed(section) {
    return this._collapsedSections?.has(section);
  }

  _toggleSection(section) {
    if (this._collapsedSections.has(section)) {
      this._collapsedSections.delete(section);
    } else {
      this._collapsedSections.add(section);
    }
    this._render();
  }

  _entityPanelKey(kind, entityId) {
    return `${kind}:${entityId}`;
  }

  _isEntityPanelCollapsed(kind, entityId) {
    return this._collapsedSections.has(this._entityPanelKey(kind, entityId));
  }

  _toggleEntityPanel(kind, entityId) {
    this._toggleSection(this._entityPanelKey(kind, entityId));
  }

  _render() {
    if (!this._hass || !this._config) {
      this.innerHTML = this._style() + `<main><p>Loading Air Scheduler...</p></main>`;
      return;
    }

    const availableEntities = this._climateEntities.filter(
      (entityId) => !this._config.entities.includes(entityId)
    );

    this.innerHTML = `
      ${this._style()}
      <main>
        <header>
          <div>
            <h1>Air Scheduler</h1>
            <p>Profile settings and schedule times for your thermostats.</p>
          </div>
          <div class="header-actions">
            <label class="toggle">
              <input type="checkbox" id="apply-on-start" ${this._config.apply_on_start ? "checked" : ""}>
              Apply current schedule on restart
            </label>
            <button class="primary" id="save" ${this._saving ? "disabled" : ""}>
              ${this._saving ? "Saving..." : "Save"}
            </button>
          </div>
        </header>

        ${this._error ? `<div class="error">${this._escape(this._error)}</div>` : ""}

        <section>
          <div class="section-head">
            <h2>Schedule</h2>
            <div class="inline-add">
              <select id="entity-picker">
                <option value="">Add thermostat...</option>
                ${availableEntities.map((entityId) => `
                  <option value="${entityId}">${this._escape(this._friendlyName(entityId))}</option>
                `).join("")}
              </select>
              <button id="add-entity">Add</button>
            </div>
          </div>
          <div class="section-body">
            ${this._renderSchedules()}
          </div>
        </section>

        <section data-section="state_settings" class="${this._isCollapsed("state_settings") ? "collapsed" : ""}">
          <div class="section-head">
            <button class="section-toggle" data-toggle-section="state_settings" aria-expanded="${!this._isCollapsed("state_settings")}">
              <span>${this._isCollapsed("state_settings") ? "▸" : "▾"}</span>
              <h2>State settings</h2>
            </button>
            <div class="profile-actions">
              <button class="primary" id="save-states" ${this._saving ? "disabled" : ""}>
                ${this._saving ? "Saving..." : "Apply all state settings"}
              </button>
            </div>
          </div>
          <div class="section-body">
            ${this._renderProfileGrid()}
          </div>
        </section>

      </main>
    `;

    this._bindEvents();
  }

  _renderProfileGrid() {
    if (!this._config.entities.length) {
      return `<p class="empty">Add thermostats before editing state settings.</p>`;
    }

    return `
      <div class="profile-grid">
        <div class="grid-head thermostat">Thermostat</div>
        ${PROFILES.map((profile) => `<div class="grid-head">${profile}</div>`).join("")}
        ${this._config.entities.map((entityId) => `
          <div class="thermostat">
            <strong>${this._escape(this._friendlyName(entityId))}</strong>
            <small>${entityId}</small>
          </div>
          ${PROFILES.map((profile) => this._renderProfileCell(profile, entityId)).join("")}
        `).join("")}
      </div>
    `;
  }

  _renderProfileCell(profile, entityId) {
    const settings = this._config.profiles[profile]?.[entityId] || {};
    const hvacMode = settings.hvac_mode || "";
    return `
      <div class="profile-cell">
        ${SETTING_FIELDS.map(([key, label]) => `
          <label>
            <span>${label}</span>
            ${key === "hvac_mode" ? `
              <select
                data-profile="${profile}"
                data-entity="${entityId}"
                data-setting="${key}"
              >
                <option value="" ${settings[key] ? "" : "selected"}>Do not change</option>
                ${this._hvacModes(entityId).map((mode) => `
                  <option value="${mode}" ${settings[key] === mode ? "selected" : ""}>${mode}</option>
                `).join("")}
              </select>
            ` : key === "fan_mode" ? `
              <div class="combo">
                <input
                  data-profile="${profile}"
                  data-entity="${entityId}"
                  data-setting="${key}"
                  value="${settings[key] ?? ""}"
                >
                <select
                  data-fan-select
                  data-profile="${profile}"
                  data-entity="${entityId}"
                  aria-label="Common fan modes"
                >
                  <option value="">Modes</option>
                  ${this._fanModes(entityId).map((mode) => `
                    <option value="${mode}">${mode}</option>
                  `).join("")}
                </select>
              </div>
            ` : `
              <input
                type="${NUMBER_FIELDS.includes(key) ? "number" : "text"}"
                ${TEMPERATURE_FIELDS.includes(key) ? "step=\"1\"" : ""}
                ${key === "humidity" ? "min=\"0\" max=\"100\" step=\"1\"" : ""}
                data-profile="${profile}"
                data-entity="${entityId}"
                data-setting="${key}"
                value="${this._isSettingDisabled(key, hvacMode) ? "" : settings[key] ?? ""}"
                ${this._isSettingDisabled(key, hvacMode) ? "disabled" : ""}
              >
            `}
          </label>
        `).join("")}
      </div>
    `;
  }

  _renderSchedules() {
    if (!this._config.entities.length) {
      return `<p class="empty">Add at least one climate entity to build a schedule.</p>`;
    }

    return `
      <div class="thermostat-schedules">
        ${this._config.entities.map((entityId) => {
          const schedules = this._scheduleEntriesForEntity(entityId);
          const calendarCollapsed = this._isEntityPanelCollapsed("calendar", entityId);
          const segmentsCollapsed = this._isEntityPanelCollapsed("segments", entityId);
          return `
            <article class="thermostat-schedule">
              <div class="thermostat-schedule-head">
                <div class="thermostat-title">
                  <strong>${this._escape(this._friendlyName(entityId))}</strong>
                  <small>${entityId}</small>
                </div>
                <div class="thermostat-actions">
                  ${schedules.length ? `
                    <button data-toggle-entity-panel="calendar" data-panel-entity="${entityId}">
                      ${calendarCollapsed ? "Show calendar" : "Hide calendar"}
                    </button>
                    <button data-toggle-entity-panel="segments" data-panel-entity="${entityId}">
                      ${segmentsCollapsed ? "Show segments" : "Hide segments"}
                    </button>
                  ` : ""}
                  <button data-add-schedule="${entityId}">Add segment</button>
                  <button class="danger" data-remove-entity="${entityId}" title="Remove thermostat">Remove</button>
                </div>
              </div>
              ${schedules.length ? `
                ${calendarCollapsed ? "" : this._renderWeekView(entityId)}
                <div class="schedule-list ${segmentsCollapsed ? "collapsed-panel" : ""}">
                  ${schedules.map(({ schedule, index }) => this._renderScheduleRow(schedule, index)).join("")}
                </div>
              ` : `<p class="empty schedule-empty">No segments yet.</p>`}
            </article>
          `;
        }).join("")}
      </div>
    `;
  }

  _renderWeekView(entityId) {
    const blocksByDay = this._weekBlocksForEntity(entityId);
    return `
      <div class="week-view" aria-label="${this._escape(this._friendlyName(entityId))} weekly schedule">
        <div class="week-header">
          <div class="week-time-head"></div>
          ${WEEK_VIEW_DAYS.map((day) => `
            <div class="week-day-head">
              <strong>${DAY_LABELS[day]}</strong>
            </div>
          `).join("")}
        </div>
        <div class="week-body">
          <div class="time-rail">
            ${Array.from({ length: 24 }, (_, hour) => `
              <span style="top: ${(hour / 24) * 100}%">${this._hourLabel(hour)}</span>
            `).join("")}
          </div>
          ${WEEK_VIEW_DAYS.map((day) => `
            <div class="week-day-column">
              ${blocksByDay.get(day).map((block) => `
                <button
                  class="week-block profile-${block.profile}"
                  data-focus-schedule="${block.index}"
                  style="top: ${block.top}%; height: ${block.height}%;"
                  title="${this._escape(`${DAY_LABELS[day]} ${this._formatMinutes(block.startMinute)}-${this._formatMinutes(block.endMinute)}: ${this._profileLabel(block.profile)} ${block.temperature}`)}"
                >
                  <span class="week-block-head">
                    <strong>${this._profileLabel(block.profile)}</strong>
                    <span>${this._escape(block.temperature)}</span>
                  </span>
                  <small>${this._formatMinutes(block.startMinute)}-${this._formatMinutes(block.endMinute)}</small>
                </button>
              `).join("")}
            </div>
          `).join("")}
        </div>
      </div>
    `;
  }

  _renderScheduleRow(schedule, index) {
    return `
      <div class="schedule-row" data-schedule-row="${index}">
        <div class="schedule-main">
          <label class="name-field">
            <span>Name</span>
            <input data-schedule="${index}" data-schedule-field="name" value="${this._escape(schedule.name || "")}">
          </label>
          <label>
            <span>State</span>
            <select data-schedule="${index}" data-schedule-field="profile">
              ${PROFILES.map((profile) => `
                <option value="${profile}" ${schedule.profile === profile ? "selected" : ""}>${profile}</option>
              `).join("")}
            </select>
          </label>
          <label>
            <span>Time</span>
            <input type="time" data-schedule="${index}" data-schedule-field="time" value="${schedule.time || "06:30"}">
          </label>
          <label class="enabled">
            <span>Enabled</span>
            <input type="checkbox" data-schedule="${index}" data-schedule-field="enabled" ${schedule.enabled !== false ? "checked" : ""}>
          </label>
          <button class="danger" data-remove-schedule="${index}">Remove</button>
        </div>
        <div class="chip-row">
          ${WEEK_VIEW_DAYS.map((day) => `
            <button class="${(schedule.days || []).includes(day) ? "selected" : ""}" data-schedule-day="${index}:${day}">
              ${day}
            </button>
          `).join("")}
        </div>
      </div>
    `;
  }

  _bindEvents() {
    this.querySelector("#save")?.addEventListener("click", () => this._saveConfig());
    this.querySelector("#save-states")?.addEventListener("click", () => this._saveConfig());
    this.querySelectorAll("[data-toggle-section]").forEach((button) => {
      button.addEventListener("click", () => this._toggleSection(button.dataset.toggleSection));
    });
    this.querySelectorAll("[data-toggle-entity-panel]").forEach((button) => {
      button.addEventListener("click", () => {
        this._toggleEntityPanel(button.dataset.toggleEntityPanel, button.dataset.panelEntity);
      });
    });
    this.querySelector("#apply-on-start")?.addEventListener("change", (event) => {
      this._config.apply_on_start = event.target.checked;
    });
    this.querySelector("#add-entity")?.addEventListener("click", () => {
      this._addEntity(this.querySelector("#entity-picker")?.value);
    });

    this.querySelectorAll("[data-remove-entity]").forEach((button) => {
      button.addEventListener("click", () => this._removeEntity(button.dataset.removeEntity));
    });
    this.querySelectorAll("[data-add-schedule]").forEach((button) => {
      button.addEventListener("click", () => this._addSchedule(button.dataset.addSchedule));
    });
    this.querySelectorAll("[data-remove-schedule]").forEach((button) => {
      button.addEventListener("click", () => this._removeSchedule(Number(button.dataset.removeSchedule)));
    });
    this.querySelectorAll("[data-profile][data-entity][data-setting]").forEach((input) => {
      const update = () => {
        this._setProfileValue(input.dataset.profile, input.dataset.entity, input.dataset.setting, input.value);
        if (input.dataset.setting === "hvac_mode") {
          this._updateTemperatureAvailability(input.dataset.profile, input.dataset.entity);
        }
      };
      input.addEventListener("input", update);
      input.addEventListener("change", update);
    });
    this.querySelectorAll("[data-fan-select]").forEach((select) => {
      select.addEventListener("change", () => {
        if (!select.value) {
          return;
        }
        const input = select.parentElement?.querySelector("[data-setting='fan_mode']");
        if (input) {
          input.value = select.value;
          this._setProfileValue(select.dataset.profile, select.dataset.entity, "fan_mode", select.value);
        }
      });
    });
    this.querySelectorAll("[data-schedule-field]").forEach((input) => {
      input.addEventListener("change", () => {
        const value = input.type === "checkbox" ? input.checked : input.value;
        this._setScheduleValue(Number(input.dataset.schedule), input.dataset.scheduleField, value);
      });
    });
    this.querySelectorAll("[data-schedule-day]").forEach((button) => {
      button.addEventListener("click", () => {
        const [index, day] = button.dataset.scheduleDay.split(":");
        this._toggleScheduleDay(Number(index), day);
      });
    });
    this.querySelectorAll("[data-focus-schedule]").forEach((button) => {
      button.addEventListener("click", () => {
        const row = this.querySelector(`[data-schedule-row="${button.dataset.focusSchedule}"]`);
        row?.scrollIntoView({ block: "center", behavior: "smooth" });
        row?.querySelector("[data-schedule-field='name']")?.focus();
      });
    });
  }

  _escape(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  _style() {
    return `
      <style>
        :host {
          display: block;
          color: var(--primary-text-color);
          background: var(--primary-background-color);
          min-height: 100vh;
        }
        main {
          max-width: 1440px;
          margin: 0 auto;
          padding: 24px;
        }
        header, .section-head, .schedule-main, .entity-pill, .chip-row, .header-actions, .profile-actions, .inline-add, .thermostat-schedule-head, .thermostat-actions {
          display: flex;
          align-items: center;
          gap: 12px;
        }
        header {
          justify-content: space-between;
          margin-bottom: 24px;
        }
        h1, h2, p {
          margin: 0;
        }
        h1 {
          font-size: 28px;
          font-weight: 600;
        }
        h2 {
          font-size: 18px;
          font-weight: 600;
        }
        header p, small, .empty {
          color: var(--secondary-text-color);
        }
        section {
          border-top: 1px solid var(--divider-color);
          padding: 20px 0;
        }
        .section-head {
          justify-content: space-between;
          margin-bottom: 14px;
        }
        .section-toggle {
          display: flex;
          align-items: center;
          gap: 8px;
          border: 0;
          background: transparent;
          padding: 0;
        }
        .section-toggle span {
          width: 18px;
          color: var(--secondary-text-color);
        }
        section.collapsed .section-body {
          display: none;
        }
        section.collapsed .inline-add,
        section.collapsed .profile-actions {
          display: none;
        }
        section.collapsed .section-head {
          margin-bottom: 0;
        }
        button, select, input {
          font: inherit;
          color: var(--primary-text-color);
        }
        button, select, input {
          border: 1px solid var(--divider-color);
          background: var(--card-background-color);
          border-radius: 6px;
          padding: 8px 10px;
        }
        input:disabled {
          background: var(--disabled-color, rgba(128, 128, 128, 0.16));
          color: var(--disabled-text-color, var(--secondary-text-color));
          opacity: 1;
        }
        button {
          cursor: pointer;
        }
        button.primary {
          background: var(--primary-color);
          color: var(--text-primary-color);
          border-color: var(--primary-color);
        }
        button.danger {
          color: var(--error-color);
        }
        button.selected {
          background: var(--primary-color);
          color: var(--text-primary-color);
          border-color: var(--primary-color);
        }
        .toggle {
          display: flex;
          align-items: center;
          gap: 8px;
          white-space: nowrap;
        }
        .error {
          color: var(--error-color);
          border: 1px solid var(--error-color);
          border-radius: 6px;
          padding: 10px;
          margin-bottom: 16px;
        }
        .entity-list {
          display: flex;
          flex-wrap: wrap;
          gap: 10px;
        }
        .entity-pill {
          border: 1px solid var(--divider-color);
          border-radius: 6px;
          padding: 8px 10px;
          background: var(--card-background-color);
        }
        .entity-pill small {
          margin-left: 4px;
        }
        .entity-pill button {
          padding: 2px 8px;
        }
        .profile-grid {
          display: grid;
          grid-template-columns: minmax(180px, 1fr) repeat(3, minmax(240px, 2fr));
          gap: 1px;
          background: var(--divider-color);
          border: 1px solid var(--divider-color);
          overflow-x: auto;
        }
        .grid-head, .thermostat, .profile-cell {
          background: var(--card-background-color);
          padding: 12px;
        }
        .grid-head {
          font-weight: 600;
          text-transform: capitalize;
        }
        .thermostat {
          display: flex;
          flex-direction: column;
          gap: 4px;
        }
        .profile-cell {
          display: grid;
          grid-template-columns: repeat(2, minmax(88px, 1fr));
          gap: 8px;
        }
        .combo {
          display: grid;
          grid-template-columns: minmax(0, 1fr) auto;
          gap: 6px;
        }
        .combo select {
          max-width: 92px;
        }
        label {
          display: flex;
          flex-direction: column;
          gap: 4px;
        }
        label span {
          color: var(--secondary-text-color);
          font-size: 12px;
        }
        .thermostat-schedules {
          display: flex;
          flex-direction: column;
          gap: 16px;
        }
        .thermostat-schedule {
          border: 1px solid var(--divider-color);
          border-radius: 6px;
          background: var(--card-background-color);
          padding: 14px;
        }
        .thermostat-schedule-head {
          justify-content: space-between;
          margin-bottom: 12px;
        }
        .thermostat-title {
          display: flex;
          flex-direction: column;
          gap: 3px;
          min-width: 0;
        }
        .thermostat-title small {
          word-break: break-word;
        }
        .thermostat-actions {
          flex-wrap: wrap;
          justify-content: flex-end;
        }
        .week-view {
          border: 1px solid var(--divider-color);
          border-radius: 6px;
          margin-bottom: 14px;
          overflow-x: auto;
          background: var(--card-background-color);
        }
        .week-header,
        .week-body {
          display: grid;
          grid-template-columns: 54px repeat(7, minmax(118px, 1fr));
          min-width: 900px;
        }
        .week-header {
          border-bottom: 1px solid var(--divider-color);
        }
        .week-time-head,
        .week-day-head {
          min-height: 42px;
          padding: 8px;
          box-sizing: border-box;
        }
        .week-day-head {
          border-left: 1px solid var(--divider-color);
        }
        .week-day-head strong {
          font-size: 13px;
        }
        .week-body {
          --week-height: 720px;
          height: var(--week-height);
          background:
            repeating-linear-gradient(
              to bottom,
              transparent 0,
              transparent 29px,
              var(--divider-color) 30px
            );
        }
        .time-rail,
        .week-day-column {
          position: relative;
          min-height: var(--week-height);
        }
        .time-rail {
          background: var(--primary-background-color);
        }
        .time-rail span {
          position: absolute;
          right: 8px;
          transform: translateY(-50%);
          color: var(--secondary-text-color);
          font-size: 11px;
          line-height: 1;
          white-space: nowrap;
        }
        .time-rail span:first-child {
          transform: translateY(0);
        }
        .week-day-column {
          border-left: 1px solid var(--divider-color);
        }
        .week-block {
          position: absolute;
          left: 6px;
          right: 6px;
          min-height: 22px;
          border-radius: 5px;
          border: 1px solid transparent;
          border-left-width: 4px;
          padding: 6px 7px;
          box-sizing: border-box;
          overflow: hidden;
          text-align: left;
          display: flex;
          flex-direction: column;
          gap: 2px;
          align-items: stretch;
          cursor: pointer;
        }
        .week-block strong,
        .week-block span,
        .week-block small {
          display: block;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .week-block .week-block-head {
          display: flex;
          align-items: baseline;
          gap: 6px;
          min-width: 0;
        }
        .week-block .week-block-head strong,
        .week-block .week-block-head span {
          min-width: 0;
        }
        .week-block .week-block-head span {
          color: var(--secondary-text-color);
          font-size: 11px;
          line-height: 1.1;
        }
        .week-block strong {
          font-size: 12px;
          line-height: 1.1;
        }
        .week-block span,
        .week-block small {
          font-size: 11px;
          line-height: 1.2;
        }
        .week-block.profile-home {
          background: rgba(47, 158, 68, 0.16);
          border-color: rgba(47, 158, 68, 0.72);
        }
        .week-block.profile-away {
          background: rgba(230, 126, 34, 0.16);
          border-color: rgba(230, 126, 34, 0.72);
        }
        .week-block.profile-sleep {
          background: rgba(52, 152, 219, 0.17);
          border-color: rgba(52, 152, 219, 0.72);
        }
        .schedule-list {
          display: flex;
          flex-direction: column;
          gap: 12px;
        }
        .collapsed-panel {
          display: none;
        }
        .schedule-row {
          border: 1px solid var(--divider-color);
          border-radius: 6px;
          background: var(--card-background-color);
          padding: 12px;
        }
        .schedule-main {
          flex-wrap: wrap;
          align-items: end;
        }
        .schedule-main label {
          min-width: 140px;
        }
        .schedule-main .name-field {
          flex: 0 0 132px;
          min-width: 112px;
        }
        .schedule-main .name-field input {
          width: 100%;
          box-sizing: border-box;
        }
        .schedule-empty {
          padding: 4px 0;
        }
        .chip-row {
          flex-wrap: wrap;
          margin-top: 10px;
        }
        .chip-row button {
          padding: 6px 9px;
        }
        @media (max-width: 900px) {
          main {
            padding: 16px;
          }
          header, .section-head, .thermostat-schedule-head {
            align-items: stretch;
            flex-direction: column;
          }
          .thermostat-actions {
            justify-content: flex-start;
          }
          .profile-grid {
            grid-template-columns: 160px repeat(3, 220px);
          }
        }
      </style>
    `;
  }
}

customElements.define("air-scheduler-panel", AirSchedulerPanel);
