import { html, LitElement, nothing } from 'lit';
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
  @state() private _isResponding: boolean = true;
  @state() private _isSnackbarOpen: boolean = false;
  @state() private _isRestarting: boolean = false;

  public hass!: HomeAssistant;
  public _config: PingCardConfig;
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

  async _ping() {
    try {
      await callFunction(
        this.hass,
        'ping',
        {},
        this._config.ping_timeout
      );
      this._isResponding = true;
      this._isSnackbarOpen = false;
      this._isRestarting = false;
      if (this._connected) {
        this._timeout = setTimeout(
          () => this._ping(),
          this._config.interval
        );
      }
    } catch (_) {
      // If the ping fails, show the snackbar (again)
      this._isResponding = false;
      this._isSnackbarOpen = true;
      // Increase ping interval if not responding
      if (this._connected) {
        this._timeout = setTimeout(() => this._ping(), 100);
      }
    }
  }

  _stopPinging() {
    clearTimeout(this._timeout);
  }

  render() {
    const _onSnackbarClose = () => this._isSnackbarOpen = false;

    return this._isResponding
      ? nothing
      : html`
          <mwc-snackbar
            ?open=${this._isSnackbarOpen}
            labelText=${this._isRestarting ? tp('restarting') : tp('error')}
            timeoutMs="-1"  <!-- Persistent until closed -->
            persistent
            @closed=${_onSnackbarClose}
          >
            ${!this._isRestarting
              ? html`<mwc-button slot="action" @click=${this._restart}>${tp('restart')}</mwc-button>`
              : nothing
            }
          </mwc-snackbar>
        `;
  }

  _resetIsRestarting() {
    this._isRestarting = false;
  }

  async _restart(event: Event) {
    event.stopPropagation(); // Prevent the click closing the snackbar
    this._isSnackbarOpen = true; // Ensure the snackbar stays open during restart
    this._isRestarting = true;
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
