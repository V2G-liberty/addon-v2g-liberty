import { mdiPencil, mdiSolarPower } from '@mdi/js';
import { html, LitElement } from 'lit';
import { customElement, state } from 'lit/decorators';
import { HomeAssistant, LovelaceCardConfig } from 'custom-card-helpers';
import { HassEvent } from 'home-assistant-js-websocket';

import { renderButton } from './util/render';
import { styles } from './card.styles';
import { callFunction } from './util/appdaemon';
import { showSolarPanelDialog } from './show-dialogs';

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
}

@customElement('v2g-liberty-solar-panels-settings-card')
export class SolarPanelsSettingsCard extends LitElement {
  @state() private _panels: SolarPanel[] = [];
  @state() private _loading: boolean = true;

  private _hass: HomeAssistant;
  private _unsubscribeSave: (() => void) | null = null;
  private _unsubscribeDelete: (() => void) | null = null;

  setConfig(config: LovelaceCardConfig) {}

  set hass(hass: HomeAssistant) {
    const firstSet = !this._hass;
    this._hass = hass;
    if (firstSet) {
      this._loadPanels();
      this._subscribeToUpdates();
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
  }

  private async _loadPanels() {
    this._loading = true;
    try {
      const data = await callFunction(this._hass, 'get_solar_panels');
      this._panels = (data.solar_panels ?? []) as SolarPanel[];
    } catch (e) {
      console.error('Failed to load solar panels', e);
      this._panels = [];
    }
    this._loading = false;
  }

  render() {
    if (this._loading) {
      return html`<ha-card header="Solar panels">
        <div class="card-content">
          <ha-circular-progress indeterminate></ha-circular-progress>
        </div>
      </ha-card>`;
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
    return html`
      <ha-settings-row>
        <span slot="heading">
          <ha-svg-icon .path=${mdiSolarPower}></ha-svg-icon>&nbsp; &nbsp;
          ${panel.name}
        </span>
        <div class="value">${summary}</div>
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
