import { LitElement, nothing } from 'lit';
import { customElement } from 'lit/decorators';
import { HomeAssistant, LovelaceCardConfig } from 'custom-card-helpers';
import { showSettingsErrorAlertDialog } from './show-dialogs';
import { hasUninitializedEntities } from './util/settings-error-alert';
import { setLanguage } from './util/translate';

// How long a settings problem has to persist before the blocking dialog opens.
// Right after a Home Assistant restart the *_settings_initialised booleans are
// briefly 'off'/'unknown' (they have no `initial:` in the package) until V2G
// Liberty writes them, and a connection sensor can be empty for a moment. A
// real misconfiguration outlasts this; a start-up window does not.
const SETTLE_DELAY_MS = 2000;

@customElement('v2g-liberty-settings-error-alert-card')
export class SettingsErrorAlertCard extends LitElement {
  private _hass: HomeAssistant;
  private _hasUninitialisedEntities: boolean | undefined = undefined;
  private _wentToSettings = false;
  private _settleTimer: number | undefined;

  connectedCallback() {
    super.connectedCallback();
    window.addEventListener('location-changed', this._handleLocationChanged);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    window.removeEventListener('location-changed', this._handleLocationChanged);
    this._cancelSettleTimer();
  }

  private _handleLocationChanged = () => {
    if (window.location.pathname.includes('/settings')) {
      this._wentToSettings = true;
    } else if (this._wentToSettings) {
      // Returning from settings page — allow the dialog to re-appear.
      this._wentToSettings = false;
      this._hasUninitialisedEntities = undefined;
      if (this._hass) this._checkUnInitialisedEntities();
    }
  };

  setConfig(config: LovelaceCardConfig) {}

  set hass(hass: HomeAssistant) {
    this._hass = hass;
    setLanguage(hass.locale?.language ?? (hass as any).language);
    this._checkUnInitialisedEntities();
  }

  private _checkUnInitialisedEntities() {
    const hasUninitialized = hasUninitializedEntities(this._hass);

    if (!hasUninitialized) {
      this._hasUninitialisedEntities = false;
      this._cancelSettleTimer();
      return;
    }
    // Already reported, or already waiting to report: nothing to do. `set hass`
    // runs on every state change, so this is the common path.
    if (hasUninitialized === this._hasUninitialisedEntities) return;
    if (this._settleTimer !== undefined) return;

    this._settleTimer = window.setTimeout(() => {
      this._settleTimer = undefined;
      // Check again instead of trusting the decision made SETTLE_DELAY_MS ago:
      // the dialog renders its list from the state of this moment, so a problem
      // that resolved in the meantime would open a dialog with an empty list.
      if (!hasUninitializedEntities(this._hass)) {
        this._hasUninitialisedEntities = false;
        return;
      }
      this._hasUninitialisedEntities = true;
      showSettingsErrorAlertDialog(this);
    }, SETTLE_DELAY_MS);
  }

  private _cancelSettleTimer() {
    if (this._settleTimer === undefined) return;
    clearTimeout(this._settleTimer);
    this._settleTimer = undefined;
  }

  protected render() {
    return nothing;
  }
}
