import { html, LitElement, nothing } from 'lit';
import { customElement, state } from 'lit/decorators';
import { HomeAssistant, LovelaceCardConfig } from 'custom-card-helpers';

import { callFunction } from './util/appdaemon';
import { partial } from './util/translate';

// Import the snackbar from MWC
import '@material/mwc-snackbar';

const tp = partial('ping-card');

interface PingCardConfig {
  ping_timeout: number;
  interval: number;
}

@customElement('v2g-liberty-ping-card')
export class PingCard extends LitElement {
  @state() private _isResponding: boolean = true;
  @state() private _snackbarOpen: boolean = false; // Snackbar open state
  @state() private _isRestarting: boolean = false; // Track restart state

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

    // Add a delay to avoid snackbar flickering
    setTimeout(() => {
      this._startPinging();
    }, this._config.interval);
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
        this._config.ping_timeout
      );
      this._isResponding = true;
      this._snackbarOpen = false; // Close snackbar if ping is successful
      this._isRestarting = false; // Reset restart state after successful ping
      if (this._connected) {
        this._timeout = setTimeout(
          () => this._ping(),
          this._config.interval
        );
      }
    } catch (_) {
      // If the ping fails, show the snackbar (again)
      this._isResponding = false;
      this._snackbarOpen = true;
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
    return this._isResponding
      ? nothing
      : html`
          <mwc-snackbar
            ?open=${this._snackbarOpen}
            labelText=${this._isRestarting ? tp('restarting') : tp('error')}
            timeoutMs="-1"  <!-- Set timeout to -1 for persistent behavior -->
            persistent  <!-- Ensure it remains visible until manually closed -->
            @closed=${this._onSnackbarClosed}
          >
            <mwc-button slot="action" @click=${this._restart}>${tp('restart')}</mwc-button>
          </mwc-snackbar>
        `;
  }

  private _onSnackbarClosed() {
    this._snackbarOpen = false; // Snackbar closed event
  }

  async _restart(event: Event) {
    event.stopPropagation(); // Prevent the click closing the snackbar
    this._isRestarting = true; // Set flag to show restart message
    this._snackbarOpen = true; // Ensure the snackbar stays open during restart

    // Attempt the restart via the Home Assistant API
    await this.hass.callWS({
      type: 'supervisor/api',
      endpoint: `/addons/9a1c9f7e_v2g-liberty/restart`,
      method: 'post',
      timeout: null,
    });

    // After the restart:
    // - Add a delay before the next ping to allow for the restart to take effect
    // - After the delay start pinging as usual
    setTimeout(() => {
      this._startPinging();
    }, this._config.interval);
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'v2g-liberty-ping-card': PingCard;
  }
}
