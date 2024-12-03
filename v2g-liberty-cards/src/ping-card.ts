import { css, html, LitElement, nothing } from 'lit';
import { customElement, state } from 'lit/decorators';
import { HomeAssistant, LovelaceCardConfig } from 'custom-card-helpers';

import { callFunction } from './util/appdaemon';
import { partial } from './util/translate';

const tp = partial('ping-card');

interface PingCardConfig {
  ping_timeout: number;
  interval: number;
}

@customElement('v2g-liberty-ping-card')
export class PingCard extends LitElement {
  @state() private _isResponding: boolean;

  public hass!: HomeAssistant;
  public _config: PingCardConfig;
  private _connected: boolean;
  private _timeout: number;

  private defaultConfig: PingCardConfig = {
    ping_timeout: 5,
    interval: 15,
  };

  setConfig(config: PingCardConfig) {
    this._config = { ...this.defaultConfig, ...config };
  }

  public connectedCallback() {
    super.connectedCallback();
    this._connected = true;
    this._startPinging();
  }

  public disconnectedCallback() {
    this._stopPinging();
    this._connected = false;
    super.disconnectedCallback();
  }

  _startPinging() {
    this._isResponding = true;
    this._timeout = setTimeout(() => this._ping(), 100);
  }

  async _ping() {
    try {
      await callFunction(
        this.hass,
        'ping',
        {},
        this._config.ping_timeout * 1000
      );
      this._isResponding = true;
      if (this._connected) {
        this._timeout = setTimeout(
          () => this._ping(),
          this._config.interval * 1000
        );
      }
    } catch (_) {
      this._isResponding = false;
      // Increase ping interval
      if (this._connected) {
        this._timeout = setTimeout(() => this._ping(), 100);
      }
    }
  }

  _stopPinging() {
    clearTimeout(this._timeout);
  }

  render() {
    return this._isResponding
      ? nothing
      : html`
          <ha-alert alert-type="error">
            <div class="error">${tp('error')}</div>
          </ha-alert>
          <p>
            <ha-markdown breaks .content=${tp('error-subtext')}></ha-markdown>
          </p>
          <mwc-button @click=${this._restart}>${tp('restart')}</mwc-button>
        `;
  }

  async _restart() {
    await this.hass.callWS({
      type: 'supervisor/api',
      endpoint: `/addons/9a1c9f7e_v2g-liberty/restart`,
      method: 'post',
      timeout: null,
    });
  }

  static styles = css`
    .error {
      font-weight: bold;
    }
  `;
}

declare global {
  interface HTMLElementTagNameMap {
    'v2g-liberty-ping-card': PingCard;
  }
}
