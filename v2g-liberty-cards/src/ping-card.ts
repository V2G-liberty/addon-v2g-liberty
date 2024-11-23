import { html, LitElement, TemplateResult, nothing } from 'lit';
import { customElement, state } from 'lit/decorators';
import { HomeAssistant, LovelaceCardConfig } from 'custom-card-helpers';

import { callFunction } from './util/appdaemon';
import { partial } from './util/translate';

const tp = partial('ping-card');

@customElement('v2g-liberty-ping-card')
export class PingCard extends LitElement {
  @state() private _isConnected: boolean;

  private _hass: HomeAssistant;
  private _timer: number;

  setConfig(config: LovelaceCardConfig) {}

  set hass(hass: HomeAssistant) {
    this._hass = hass;
    this._isConnected = true;
  }

  public connectedCallback() {
    super.connectedCallback();
    this._startPinging();
  }

  public disconnectedCallback() {
    this._stopPinging();
    super.disconnectedCallback();
  }

  _startPinging() {
    this._ping();
    this._timer = setInterval(() => this._ping(), 10 * 1000);
  }

  async _ping() {
    try {
      await callFunction(this._hass, 'ping', {}, 5 * 1000);
    } catch (err) {
      this._isConnected = false;
    }
  }

  _stopPinging() {
    clearInterval(this._timer);
  }

  render() {
    return this._isConnected
      ? nothing
      : html`<ha-alert alert-type="error">${tp('error')}</ha-alert>`;
  }
}
