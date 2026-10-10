import { mdiAlertCircle, mdiPencil, mdiSolarPower } from '@mdi/js';
import { html, LitElement } from 'lit';
import { customElement, state } from 'lit/decorators';
import { HomeAssistant, LovelaceCardConfig } from 'custom-card-helpers';
import { HassEvent } from 'home-assistant-js-websocket';

import { renderButton, renderLoadFailedCard } from './util/render';
import { styles } from './card.styles';
import { callFunction, SETTINGS_LOAD_TIMEOUT_MS } from './util/appdaemon';
import { showSolarPanelDialog } from './show-dialogs';
import * as entityIds from './entity-ids';

interface SolarPanel {
  id: string;
  name: string;
  phases: 1 | 3;
  connected_to_phase?: 1 | 2 | 3;
  peak_power_wp?: number;
  curtailable?: boolean;
  power_entity_id: string;
  curtail_entity_id?: string;
  fm_asset_id?: number;
  fm_sensor_id?: number;
  // Server-computed: non-null when the panel no longer matches the current
  // grid configuration (e.g. phases > grid_phases after a grid change).
  inconsistency_reason?: string | null;
}

@customElement('v2g-liberty-solar-panels-settings-card')
export class SolarPanelsSettingsCard extends LitElement {
  @state() private _panels: SolarPanel[] = [];
  @state() private _loading: boolean = true;
  @state() private _loadFailed: boolean = false;

  private _hass: HomeAssistant;
  private _rebootedAt: string | null = null;
  private _unsubscribeSave: (() => void) | null = null;
  private _unsubscribeDelete: (() => void) | null = null;
  // Inconsistency_reason depends on grid phases, so also refresh on grid
  // save events — otherwise the alert icon would only appear after a
  // manual page reload.
  private _unsubscribeGridSave: (() => void) | null = null;

  setConfig(config: LovelaceCardConfig) {}

  set hass(hass: HomeAssistant) {
    const firstSet = !this._hass;
    this._hass = hass;
    if (firstSet) {
      this._rebootedAt = hass.states[entityIds.appRebootedAt]?.state ?? null;
      this._loadPanels();
      this._subscribeToUpdates();
      return;
    }
    // The add-on stamps this at the end of every start-up. Reloading on a
    // change covers the page being open (or refreshed) while the add-on was
    // still starting, and the add-on restarting afterwards.
    const rebootedAt = hass.states[entityIds.appRebootedAt]?.state ?? null;
    if (rebootedAt && rebootedAt !== this._rebootedAt) {
      this._rebootedAt = rebootedAt;
      this._loadPanels();
    }
  }

  private async _subscribeToUpdates() {
    this._unsubscribeSave =
      await this._hass.connection.subscribeEvents<HassEvent>(
        () => this._loadPanels(),
        'save_solar_panel.result'
      );
    this._unsubscribeDelete =
      await this._hass.connection.subscribeEvents<HassEvent>(
        () => this._loadPanels(),
        'delete_solar_panel.result'
      );
    this._unsubscribeGridSave =
      await this._hass.connection.subscribeEvents<HassEvent>(
        () => this._loadPanels(),
        'save_grid_connection_settings.result'
      );
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    if (this._unsubscribeSave) {
      this._unsubscribeSave();
      this._unsubscribeSave = null;
    }
    if (this._unsubscribeDelete) {
      this._unsubscribeDelete();
      this._unsubscribeDelete = null;
    }
    if (this._unsubscribeGridSave) {
      this._unsubscribeGridSave();
      this._unsubscribeGridSave = null;
    }
  }

  private async _loadPanels() {
    this._loading = true;
    try {
      const data = await callFunction(
        this._hass,
        'get_solar_panels',
        {},
        SETTINGS_LOAD_TIMEOUT_MS
      );
      this._panels = (data.solar_panels ?? []) as SolarPanel[];
      this._loadFailed = false;
    } catch (e) {
      // Not reaching the add-on is not the same as having no panels: showing
      // the empty state would invite the user to add panels that already
      // exist. Say what happened and offer a Retry.
      console.error('Failed to load solar panels', e);
      this._panels = [];
      this._loadFailed = true;
    }
    this._loading = false;
  }

  render() {
    if (this._loading) {
      return html`<ha-card header="Solar panels">
        <div class="card-content">
          <ha-spinner></ha-spinner>
        </div>
      </ha-card>`;
    }

    if (this._loadFailed) {
      return renderLoadFailedCard(this._hass, 'Solar panels', () =>
        this._loadPanels()
      );
    }

    const content =
      this._panels.length === 0
        ? this._renderEmptyContent()
        : this._renderPanelList();
    return html`<ha-card header="Solar panels">${content}</ha-card>`;
  }

  private _renderEmptyContent() {
    return html`
      <div class="card-content">
        <ha-alert alert-type="info"
          >No solar panels configured. This is optional.</ha-alert
        >
        <p>
          Prepare for the end of net metering by letting V2G Liberty learn your
          solar generation patterns.
        </p>
      </div>
      <div class="card-actions">
        ${renderButton(
          this._hass,
          () => this._openDialog(),
          true,
          'Add solar panels'
        )}
      </div>
    `;
  }

  private _renderPanelList() {
    return html`
      <div class="card-content">
        ${this._panels.map((p) => this._renderPanelRow(p))}
      </div>
      <div class="card-actions">
        ${renderButton(
          this._hass,
          () => this._openDialog(),
          true,
          'Add panel'
        )}
      </div>
    `;
  }

  private _renderPanelRow(panel: SolarPanel) {
    const wp = panel.peak_power_wp ? `${panel.peak_power_wp} Wp` : '';
    const phases = panel.phases === 1 ? '1-phase' : '3-phase';
    const phaseInfo =
      panel.phases === 1 && panel.connected_to_phase
        ? `${phases} (L${panel.connected_to_phase})`
        : phases;
    const summary = [wp, phaseInfo].filter(Boolean).join(', ');
    // Use the alert icon as the leading icon when inconsistent — that way
    // a long panel name can't push the warning off-screen, and the row
    // catches the eye at a glance.
    const leadingIcon = panel.inconsistency_reason
      ? html`<ha-svg-icon
          .path=${mdiAlertCircle}
          title=${panel.inconsistency_reason}
          style="color: var(--error-color);"
        ></ha-svg-icon>`
      : html`<ha-svg-icon .path=${mdiSolarPower}></ha-svg-icon>`;
    // Same shape as every other settings row, but with its own leading icon:
    // a panel can carry an error marker instead of the solar symbol.
    return html`
      <ha-settings-row>
        <span slot="heading" style="display: flex; align-items: center;">
          ${leadingIcon}
          <span
            style="margin-left: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;"
            >${panel.name}</span
          >
        </span>
        <div
          class="text-content value state"
          style="flex: 0 0 auto; white-space: nowrap;"
        >
          ${summary}
        </div>
        <ha-icon-button
          .label=${'Edit'}
          .path=${mdiPencil}
          @click=${() => this._openDialog(panel)}
        ></ha-icon-button>
      </ha-settings-row>
    `;
  }

  private _openDialog(panel?: SolarPanel) {
    showSolarPanelDialog(this, panel ? { panel } : {});
  }

  static styles = [styles];
}
