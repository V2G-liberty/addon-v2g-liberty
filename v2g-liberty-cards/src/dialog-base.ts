import { LitElement, html, nothing } from 'lit';
import { property, state } from 'lit/decorators';
import { HomeAssistant, fireEvent } from 'custom-card-helpers';
import { HassEntity } from 'home-assistant-js-websocket';

import { callFunction } from './util/appdaemon';
import { renderButton } from './util/render';

export function defaultState(
  stateObj: HassEntity,
  defaultValue: string
): string {
  return isUninitialised(stateObj) ? defaultValue : stateObj.state;
}

function isUninitialised(stateObj: HassEntity): boolean {
  return stateObj.state === 'unknown';
}

export abstract class DialogBase extends LitElement {
  @property({ attribute: false }) public hass!: HomeAssistant;

  @state() protected isOpen: boolean;

  // ── FlexMeasures reachability gate ──────────────────────────────────
  // Shared by dialogs whose Save provisions assets/sensors on the Smart
  // schedule server (FlexMeasures) — grid connection, solar panels, and
  // (planned, branch 359) the charger. Such a dialog must not proceed while
  // that server is unreachable. The fm_connection_status sensor is a lagging
  // indicator (no heartbeat), so we probe the live client fresh on open and on
  // demand via "Check again".
  @state() protected _fmProbe: 'checking' | 'reachable' | 'unreachable' =
    'checking';

  protected get _fmReachable(): boolean {
    return this._fmProbe === 'reachable';
  }

  // Fire from a subclass's showDialog() (non-blocking). Updates _fmProbe when
  // it resolves, so the gate opens/closes reactively.
  protected async _probeFm(): Promise<void> {
    this._fmProbe = 'checking';
    try {
      const result = await callFunction(this.hass, 'test_fm_reachable');
      this._fmProbe = result?.reachable ? 'reachable' : 'unreachable';
    } catch (e) {
      // Add-on unreachable or probe errored → treat as not reachable.
      this._fmProbe = 'unreachable';
    }
  }

  // Renders the shared reachability gate: a "checking" note, or a blocking
  // error alert plus a "Check again" action while unreachable; `nothing` once
  // reachable. `subject` names what is being set up (e.g. "grid connection",
  // "solar panel"). Subclasses gate their Continue/Save on `_fmReachable`.
  protected _renderFmGate(subject: string) {
    if (this._fmProbe === 'checking') {
      return html`<ha-alert alert-type="info">
        Checking the Smart schedule server…
      </ha-alert>`;
    }
    if (this._fmProbe === 'unreachable') {
      return html`
        <ha-alert
          alert-type="error"
          title="Smart schedule server not reachable"
        >
          The ${subject} is created on the Smart schedule server (FlexMeasures),
          so it can only be set up while that server is reachable — and right now
          it is not.
          <p style="margin: 8px 0 0 0;"><strong>To continue:</strong></p>
          <ul style="margin: 4px 0 0 16px; padding: 0;">
            <li>Restore the connection under <strong>Smart schedule</strong>.</li>
            <li>
              Reopen this dialog afterwards — the connection is checked again
              automatically when you do.
            </li>
          </ul>
        </ha-alert>
        ${renderButton(this.hass, () => this._probeFm(), false, 'Check again')}
      `;
    }
    return nothing;
  }

  public async showDialog(): Promise<void> {
    this.isOpen = true;
  }

  public closeDialog(): void {
    this.isOpen = false;
    fireEvent(this, 'dialog-closed', { dialog: this.localName });
  }
}
