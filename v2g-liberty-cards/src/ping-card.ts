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

// How long the add-on has to stay unreachable before the toast appears.
// A Home Assistant restart takes the app with it: AppDaemon reconnects and only
// then re-initialises the apps, so the ping has nobody to answer it for a few
// seconds -- measured on 2026-09-21: HA back at 09:52:07, the app serving again
// at 09:52:11. Alarming in that window blames the add-on for a restart it did
// not choose, and tells the user to restart something that is already coming
// back. A genuinely dead add-on outlasts this.
//
// 20 s is a little over twice the measured window, chosen over 30 s to report a
// real outage sooner. Do not go much lower: on slower hardware with a real
// charger, AppDaemon's 5 s reconnect retry plus the app's initialisation (which
// includes a Modbus connection test) can take noticeably longer than it does on
// a dev machine.
const ALARM_AFTER_MS = 20000;

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
  // When the current run of failures started; null while the app is answering.
  private _failingSince: number | null = null;

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

  // Whether the frontend itself still has a connection to Home Assistant.
  // Ask the socket, not `hass.connected`: while HA is away the frontend stops
  // handing cards a fresh `hass`, so the copy this card holds keeps saying
  // `connected: true`. The same stale object still references the live
  // Connection, whose `connected` is a getter over the actual socket.
  private get _haConnected(): boolean {
    const connection = (this.hass as any)?.connection;
    if (connection && typeof connection.connected === 'boolean') {
      return connection.connected;
    }
    return (this.hass as any)?.connected ?? true;
  }

  async _ping() {
    // While Home Assistant itself is away, a failing ping says nothing about the
    // add-on: it travels over the very connection that is down. Do not even try,
    // and leave an already-shown toast alone -- if the add-on really was
    // unreachable before HA went away, that is still true.
    if (!this._haConnected) {
      if (this._connected) {
        this._timeout = setTimeout(() => this._ping(), 1000);
      }
      return;
    }

    try {
      await callFunction(
        this.hass,
        'ping',
        {},
        this._config.ping_timeout
      );
      this._isResponding = true;
      this._isRestarting = false;
      this._failingSince = null;
      this._toast?.hide('dismiss');
      if (this._connected) {
        this._timeout = setTimeout(
          () => this._ping(),
          this._config.interval
        );
      }
    } catch (_) {
      if (!this._haConnected) {
        // Home Assistant went away while this ping was in flight: HA's problem,
        // not the add-on's. Try again once the connection is back.
        if (this._connected) {
          this._timeout = setTimeout(() => this._ping(), 1000);
        }
        return;
      }
      if (this._failingSince === null) this._failingSince = Date.now();
      const failingFor = Date.now() - this._failingSince;
      if (failingFor < ALARM_AFTER_MS && !this._isRestarting) {
        // Too early to blame anyone; keep trying so recovery stays instant.
        if (this._connected) {
          this._timeout = setTimeout(() => this._ping(), 1000);
        }
        return;
      }
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
              ? html`<ha-button slot="action" @click=${this._restart} appearance="outlined" size="s">${tp('restart')}</ha-button>`
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
