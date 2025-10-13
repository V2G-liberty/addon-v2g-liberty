import { html, LitElement } from 'lit';
import { customElement, state } from 'lit/decorators';
import { HassEntity } from 'home-assistant-js-websocket';
import { HomeAssistant, LovelaceCardConfig } from 'custom-card-helpers';

import { callFunction } from './util/appdaemon';

import { renderEntityBlock, renderButton, renderSpinner } from './util/render';
import { partial, t } from './util/translate';
import { styles } from './card.styles';
import { showAdministratorSettingsDialog } from './show-dialogs';
import * as entityIds from './entity-ids';

const tp = partial('settings.administrator');
const tc = partial('settings.common');
const tt = partial('settings.test_notification');

type TestNotificationState = 'idle' | 'waiting' | 'timeout' | 'success';

@customElement('v2g-liberty-administrator-settings-card')
export class AdministratorSettingsCard extends LitElement {
  @state() private _adminSettingsInitialised: HassEntity;
  @state() private _adminMobileName: HassEntity;
  @state() private _adminMobilePlatform: HassEntity;
  @state() private _testNotificationState: TestNotificationState = 'idle';
  private _resetToIdleTimerId: number | null = null;
  private _hass: HomeAssistant;

  setConfig(config: LovelaceCardConfig) {}

  set hass(hass: HomeAssistant) {
    this._hass = hass;
    this._adminSettingsInitialised =
      hass.states[entityIds.adminSettingsInitialised];
    this._adminMobileName = hass.states[entityIds.adminMobileName];
    this._adminMobilePlatform = hass.states[entityIds.adminMobilePlatform];
  }

  render() {
    const isInitialised = this._adminSettingsInitialised.state === 'on';
    const content = isInitialised
      ? this._renderInitialisedContent()
      : this._renderUninitialisedContent();
    return html`<ha-card header="${tp('header')}">${content}</ha-card>`;
  }

  private async _sendNotificationCallback(): Promise<void> {
    this._testNotificationState = 'waiting';

    if (this._resetToIdleTimerId !== null) {
      clearTimeout(this._resetToIdleTimerId);
      this._resetToIdleTimerId = null;
    }

    const args = {
      notificationTitle: tt('notification-title'),
      notificationMessage: tt('notification-message'),
      notificationButtonLabel: tt('notification-button-label'),
    };

    try {
      const result = await callFunction(
        this._hass,
        'send_test_notification',
        args,
        2 * 60 * 1000,
      );
      this._testNotificationState = 'success';
    } catch (err) {
      this._testNotificationState = 'timeout';
    }
    this._resetToIdleTimerId = window.setTimeout(() => {
      this._testNotificationState = 'idle';
      this._resetToIdleTimerId = null;
    }, 1 * 60 * 1000);
  }



  private _renderInitialisedContent() {
    const editCallback = () => showAdministratorSettingsDialog(this);

    return html`
      <div class="card-content">
        <div class="description">${tp('sub-header')}</div>
        ${renderEntityBlock(this._adminMobileName)}
        ${renderEntityBlock(this._adminMobilePlatform)}
      </div>
      <div class="card-content" style="margin-top: 1em;">
        ${this._renderTestNotificationPart()}
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

  private _renderTestNotificationPart() {
    if (this._testNotificationState === 'idle') {
      return html`
          <ha-button @click=${this._sendNotificationCallback} appearance='plain' variant='brand' size='small'>
            <ha-icon slot='start' icon='mdi:cellphone-text'></ha-icon>
            ${tt('send-test-notification')}
          </ha-button>
        `;
    }

    if (this._testNotificationState === 'waiting') {
      return html`
        <div style="display: flex; border: 2px solid var(--warning-color); padding: 1em; border-radius: 4px;">
        ${renderSpinner()}
        <div style="padding-left: 0.5em;">${tt('how-to-react-on-mobile-device')}</div></div>
      `;
    }

    if (this._testNotificationState === 'timeout') {
      return html`
        <ha-alert alert-type="error">${tt('test-notification-timeout')}</ha-alert>
      `;
    }

    if (this._testNotificationState === 'success') {
      return html`
        <ha-alert alert-type="success">${tt('test-notification-success')}</ha-alert>
      `;
    }
  }


  private _renderUninitialisedContent() {
    const editCallback = () => showAdministratorSettingsDialog(this);

    return html`
      <div class="card-content">
        <ha-alert alert-type="warning">${tp('alert')}</ha-alert>
        <div class="description">${tp('sub-header')}</div>
      </div>
      <div class="card-actions">
        ${renderButton(
          this._hass,
          editCallback,
          true,
          tc('configure')
        )}
      </div>
    `;
  }

  static styles = styles;
}
