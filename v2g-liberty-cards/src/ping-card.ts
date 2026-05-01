import { html, LitElement, nothing } from 'lit';
import { customElement, state } from 'lit/decorators';
import { HomeAssistant, LovelaceCardConfig } from 'custom-card-helpers';

import { callFunction } from './util/appdaemon';
import { partial, setLanguage } from './util/translate';

const tp = partial('ping-card');

interface PingCardConfig {
  ping_timeout: number;
  interval: number;
}

@customElement('v2g-liberty-ping-card')
export class PingCard extends LitElement {
  @state() private _isResponding: boolean = true;
  @state() private _isRestarting: boolean = false;

  private _hass: HomeAssistant;
  public _config: PingCardConfig;

  set hass(hass: HomeAssistant) {
    this._hass = hass;
    setLanguage(hass.locale?.language ?? (hass as any).language);
  }

  get hass(): HomeAssistant {
    return this._hass;
  }

  private _connected: boolean;
  private _timeout: number;

  // Timings in milliseconds
  private defaultConfig: PingCardConfig = {
    ping_timeout: 5000,
    interval: 15000,
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
    this._timeout = setTimeout(() => this._ping(), 1000);
  }

  private get _toast(): any | null {
    return this.renderRoot?.querySelector('ha-toast') ?? null;
  }

  async _ping() {
    try {
      await callFunction(
        this.hass,
        'ping',
        {},
        this._config.ping_timeout
      );
      this._isResponding = true;
      this._isRestarting = false;
      this._toast?.hide('dismiss');
      if (this._connected) {
        this._timeout = setTimeout(
          () => this._ping(),
          this._config.interval
        );
      }
    } catch (_) {
      // If the ping fails, show the toast (again)
      this._isResponding = false;
      if (this._connected) {
        await this.updateComplete;
        this._showToast();
        this._timeout = setTimeout(() => this._ping(), 100);
      }
    }
  }

  private _showToast() {
    const toast = this._toast;
    if (!toast) return;
    toast.labelText = this._isRestarting ? tp('restarting') : tp('error');
    toast.show();
  }

  _stopPinging() {
    clearTimeout(this._timeout);
  }

  render() {
    return this._isResponding
      ? nothing
      : html`
          <ha-toast .timeoutMs=${-1}>
            ${!this._isRestarting
              ? html`<ha-button slot="action" @click=${this._restart} appearance="outlined" size="small">${tp('restart')}</ha-button>`
              : nothing
            }
          </ha-toast>
        `;
  }

  _resetIsRestarting() {
    this._isRestarting = false;
  }

  async _restart(event: Event) {
    event.stopPropagation();
    this._isRestarting = true;
    this._showToast();
    // After the restart assume that ultimately after a timeout the restart
    // should be finished and if not show an error again if pinging fails.
    setTimeout(() => this._resetIsRestarting(), this._config.interval * 2);

    // Attempt can fail so should be at end of this function
    await this.hass.callWS({
      type: 'supervisor/api',
      endpoint: `/addons/9a1c9f7e_v2g-liberty/restart`,
      method: 'post',
      timeout: null,
    });
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'v2g-liberty-ping-card': PingCard;
  }
}
