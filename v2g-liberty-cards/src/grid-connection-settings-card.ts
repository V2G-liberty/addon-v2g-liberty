import { css, html, LitElement, nothing } from 'lit';
import { customElement, state } from 'lit/decorators';
import { HomeAssistant, LovelaceCardConfig } from 'custom-card-helpers';

import { renderButton } from './util/render';
import { styles } from './card.styles';
import { callFunction } from './util/appdaemon';
import { showGridConnectionSettingsDialog } from './show-dialogs';

@customElement('v2g-liberty-grid-connection-settings-card')
export class GridConnectionSettingsCard extends LitElement {
  @state() private _isConfigured: boolean = false;
  @state() private _phases: number | null = null;
  @state() private _capacityPerPhase: number | null = null;
  @state() private _consumptionEntities: string[] = [];
  @state() private _productionEntities: string[] = [];
  @state() private _loading: boolean = true;

  private _hass: HomeAssistant;

  setConfig(config: LovelaceCardConfig) {}

  set hass(hass: HomeAssistant) {
    const firstSet = !this._hass;
    this._hass = hass;
    if (firstSet) {
      this._loadSettings();
    }
  }

  private async _loadSettings() {
    this._loading = true;
    try {
      const data = await callFunction(this._hass, 'get_grid_connection_settings');
      this._phases = data.phases ?? null;
      this._capacityPerPhase = data.capacity_per_phase ?? null;
      this._consumptionEntities = data.consumption_entities ?? [];
      this._productionEntities = data.production_entities ?? [];
      this._isConfigured = this._consumptionEntities.length > 0;
    } catch (e) {
      console.error('Failed to load grid connection settings', e);
      this._isConfigured = false;
    }
    this._loading = false;
  }

  render() {
    if (this._loading) {
      return html`<ha-card header="Grid connection">
        <div class="card-content">
          <ha-circular-progress indeterminate></ha-circular-progress>
        </div>
      </ha-card>`;
    }

    const content = this._isConfigured
      ? this._renderConfiguredContent()
      : this._renderEmptyContent();
    return html`<ha-card header="Grid connection">${content}</ha-card>`;
  }

  private _renderEmptyContent() {
    const editCallback = () => this._openDialog();
    return html`
      <div class="card-content">
        <p>Not yet configured. This is optional.</p>
        <p>Track your household energy usage to improve charging schedules over time.</p>
      </div>
      <div class="card-actions">
        ${renderButton(this._hass, editCallback, true, 'Set up')}
      </div>
    `;
  }

  private _renderConfiguredContent() {
    const editCallback = () => this._openDialog();
    const phaseLabel = this._phases === 1 ? '1-phase' : '3-phase';
    return html`
      <div class="card-content">
        <p>${phaseLabel}, ${this._capacityPerPhase}A per phase</p>
        <div class="entity-list">
          <p><strong>Consumption</strong></p>
          ${this._consumptionEntities.map(
            (e, i) => html`<p class="entity-id">L${i + 1}: ${e}</p>`
          )}
          <p><strong>Production</strong></p>
          ${this._productionEntities.map(
            (e, i) => html`<p class="entity-id">L${i + 1}: ${e}</p>`
          )}
        </div>
      </div>
      <div class="card-actions">
        ${renderButton(
          this._hass,
          editCallback,
          true,
          this._hass.localize('ui.common.edit')
        )}
      </div>
    `;
  }

  private _openDialog() {
    showGridConnectionSettingsDialog(this);
    // Reload settings when dialog closes
    this.addEventListener(
      'dialog-closed',
      () => this._loadSettings(),
      { once: true }
    );
  }

  static styles = [
    styles,
    css`
      .entity-id {
        font-family: var(--code-font-family, monospace);
        font-size: 0.9em;
        margin: 2px 0;
      }
      .entity-list {
        margin-top: 8px;
      }
    `,
  ];
}
