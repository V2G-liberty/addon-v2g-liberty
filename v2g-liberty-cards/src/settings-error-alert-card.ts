import { LitElement, nothing } from 'lit';
import { customElement } from 'lit/decorators';
import { HomeAssistant, LovelaceCardConfig } from 'custom-card-helpers';
import { showSettingsErrorAlertDialog } from './show-dialogs';
import { hasUninitializedEntities } from './util/settings-error-alert';
import { setLanguage } from './util/translate';

@customElement('v2g-liberty-settings-error-alert-card')
export class SettingsErrorAlertCard extends LitElement {
  private _hass: HomeAssistant;
  private _hasUninitialisedEntities: boolean | undefined = undefined;
  private _wentToSettings = false;

  connectedCallback() {
    super.connectedCallback();
    window.addEventListener('location-changed', this._handleLocationChanged);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    window.removeEventListener('location-changed', this._handleLocationChanged);
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
    if (hasUninitialized && hasUninitialized !== this._hasUninitialisedEntities) {
      this._hasUninitialisedEntities = hasUninitialized;
      // Defer one frame so the Lovelace dialog manager is ready.
      requestAnimationFrame(() => showSettingsErrorAlertDialog(this));
    } else if (!hasUninitialized) {
      this._hasUninitialisedEntities = false;
    }
  }

  protected render() {
    return nothing;
  }
}
