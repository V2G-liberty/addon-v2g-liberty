import { mdiCheck } from '@mdi/js';
import { css, html, nothing } from 'lit';
import { customElement, state } from 'lit/decorators';
import { HassEntity } from 'home-assistant-js-websocket';

import { callFunction } from './util/appdaemon';
import {
  InputText,
  renderDialogHeader,
  renderInputPassword,
  renderInputSelect,
  renderInputText,
  renderSelectOptionWithLabel,
} from './util/render';
import { partial } from './util/translate';
import { defaultState, DialogBase } from './dialog-base';
import * as entityIds from './entity-ids';

export const tagName = 'edit-car-reservation-calendar-settings-dialog';
const tp = partial('settings.car-reservation-calendar');

enum CaldavConnectionStatus {
  Connected = 'Successfully connected',
  Connecting = 'Trying to connect...',
  Failed = 'Failed to connect',
  TimedOut = 'Timed out',
}

@customElement(tagName)
class EditCarReservationCalendarSettingsDialog extends DialogBase {
  @state() private _currentPage: string;
  @state() private _carCalendarSource: string;
  @state() private _calendarAccountUrl: string;
  @state() private _calendarAccountUsername: string;
  @state() private _calendarAccountPassword: string;
  @state() private _carCalendarName: string;
  @state() private _integrationCalendarEntityName: string;
  @state() private _caldavConnectionStatus: string;

  private _caldavCalendars: string[];
  private _homeAssistantCalendars: HassEntity[];
  private _hasTriedToConnectToCaldav: boolean;

  public async showDialog(): Promise<void> {
    super.showDialog();
    this._currentPage = 'source-selection';
    this._carCalendarSource =
      this.hass.states[entityIds.carCalendarSource].state;
    this._calendarAccountUrl = defaultState(
      this.hass.states[entityIds.calendarAccountUrl],
      ''
    );
    this._calendarAccountUsername = defaultState(
      this.hass.states[entityIds.calendarAccountUsername],
      ''
    );
    this._calendarAccountPassword = defaultState(
      this.hass.states[entityIds.calendarAccountPassword],
      ''
    );
    this._carCalendarName = this.hass.states[entityIds.carCalendarName].state;
    this._integrationCalendarEntityName =
      this.hass.states[entityIds.integrationCalendarEntityName].state;
    this._caldavConnectionStatus = '';
    this._hasTriedToConnectToCaldav = false;
    await this.updateComplete;
  }

  protected render() {
    if (!this.isOpen) return nothing;

    const content =
      this._currentPage === 'source-selection'
        ? this._renderCarCalendarSourceSelection()
        : this._currentPage === 'caldav-calendar'
        ? this._renderCaldavCalendar()
        : this._currentPage === 'homeassistant-calendar'
        ? this._renderHomeAssistantCalendarSelection()
        : nothing;
    return html`
      <ha-dialog
        open
        @closed=${this.closeDialog}
        .heading=${renderDialogHeader(this.hass, tp('header'))}
      >
        ${content}
      </ha-dialog>
    `;
  }

  private _renderCarCalendarSourceSelection() {
    const description = tp('source-selection.description');
    const selectName = tp('source-selection.select-name');
    const stateObj = this.hass.states[entityIds.carCalendarSource];
    const current = this._carCalendarSource;
    const changedCallback = evt => (this._carCalendarSource = evt.target.value);
    const isSelectionValid = stateObj.attributes.options.includes(current);

    return html`
      <p>${description}</p>
      <p>
        <div>
          <span class="select-name">${selectName}</span>
        </div>
      </p>
      <div class="select-options">
        ${stateObj.attributes.options.map(option => {
          const label = html`
            <span
              >${option}
              <div class="option-description">
                ${tp(`source-selection.${option}-description`)}
              </div>
            </span>
          `;
          return html`<p>
            ${renderSelectOptionWithLabel(
              option,
              label,
              option === current,
              changedCallback
            )}
          </p>`;
        })}
      </div>
      <mwc-button
        @click=${this._continue}
        ?disabled=${!isSelectionValid}
        slot="primaryAction"
      >
        ${this.hass.localize('ui.common.continue')}
      </mwc-button>
    `;
  }

  private _renderCaldavCalendar() {
    return this._isConnectedToCaldav()
      ? this._renderCaldavCalendarSelection()
      : this._renderCaldavAccountDetails();
  }

  private _isConnectedToCaldav() {
    return this._caldavConnectionStatus === CaldavConnectionStatus.Connected;
  }

  private _isBusyConnecting() {
    return this._caldavConnectionStatus === CaldavConnectionStatus.Connecting;
  }

  private _renderCaldavAccountDetails() {
    const description = tp('caldav.description');

    const calendarAccountUrlState =
      this.hass.states[entityIds.calendarAccountUrl];
    const calendarAccountUsernameState =
      this.hass.states[entityIds.calendarAccountUsername];
    const calendarAccountPasswordState =
      this.hass.states[entityIds.calendarAccountPassword];

    return html`
      <ha-markdown breaks .content=${description}></ha-markdown>
      ${renderInputText(
        InputText.URL,
        this._calendarAccountUrl,
        calendarAccountUrlState,
        evt => (this._calendarAccountUrl = evt.target.value)
      )}
      ${renderInputText(
        InputText.EMail,
        this._calendarAccountUsername,
        calendarAccountUsernameState,
        evt => (this._calendarAccountUsername = evt.target.value)
      )}
      ${renderInputPassword(
        this._calendarAccountPassword,
        calendarAccountPasswordState,
        evt => (this._calendarAccountPassword = evt.target.value)
      )}
      ${this._renderConnectionError()}
      <mwc-button @click=${this._back} slot="secondaryAction">
        &lt; ${this.hass.localize('ui.common.back')}
      </mwc-button>
      ${this._isBusyConnecting()
        ? html`
            <ha-circular-progress
              size="small"
              indeterminate
              slot="primaryAction"
            ></ha-circular-progress>
          `
        : html`
            <mwc-button @click=${this._continueCaldav} slot="primaryAction">
              ${this.hass.localize('ui.common.continue')}
            </mwc-button>
          `}
    `;
  }

  private _renderConnectionError() {
    const hasConnectionError = this._caldavConnectionStatus;
    return !hasConnectionError || this._isBusyConnecting()
      ? nothing
      : html`<ha-alert alert-type="error">${hasConnectionError}</ha-alert>`;
  }

  private _renderCaldavCalendarSelection() {
    const nrOfCalendars = this._caldavCalendars.length;
    return nrOfCalendars === 0
      ? this._renderCaldavNoCalendar()
      : nrOfCalendars === 1
      ? this._renderCaldavOneCalendar()
      : this._renderCaldavMultipleCalendars();
  }

  private _renderCaldavNoCalendar() {
    return html`
      ${this._renderLoginSuccessful()}
      <ha-alert alert-type="error">${tp('caldav.error')}</ha-alert>
      <mwc-button @click=${this._back} slot="secondaryAction">
        &lt; ${this.hass.localize('ui.common.back')}
      </mwc-button>
    `;
  }
  private _renderLoginSuccessful() {
    return html`
      <div class="success">
        <ha-svg-icon .path=${mdiCheck}></ha-svg-icon>
        <span>Login successful</span>
      </div>
    `;
  }

  private _renderCaldavOneCalendar() {
    // Assign the only possible choice without asking user
    this._carCalendarName = this._caldavCalendars[0];

    return html`
      ${this._renderLoginSuccessful()}
      <strong>Calendar name</strong>
      <div>${this._carCalendarName}</div>
      <mwc-button @click=${this._back} slot="secondaryAction">
        &lt; ${this.hass.localize('ui.common.back')}
      </mwc-button>
      <mwc-button @click=${this._save} slot="primaryAction">
        ${this.hass.localize('ui.common.save')}
      </mwc-button>
    `;
  }

  private _renderCaldavMultipleCalendars() {
    const description = tp('homeassistant.description');
    const carCalendarNameState = this.hass.states[entityIds.carCalendarName];

    return html`
      ${this._renderLoginSuccessful()}
      <p><ha-markdown breaks .content=${description}></ha-markdown></p>
      ${renderInputSelect(
        this._carCalendarName,
        // TODO: turn into input_text
        carCalendarNameState,
        evt => (this._carCalendarName = evt.target.value),
        this._caldavCalendars
      )}
      <mwc-button @click=${this._back} slot="secondaryAction">
        &lt; ${this.hass.localize('ui.common.back')}
      </mwc-button>
      <mwc-button @click=${this._save} slot="primaryAction">
        ${this.hass.localize('ui.common.save')}
      </mwc-button>
    `;
  }

  private _renderHomeAssistantCalendarSelection() {
    const nrOfCalendars = this._homeAssistantCalendars.length;
    return nrOfCalendars === 0
      ? this._renderHomeAssistantNoCalendar()
      : nrOfCalendars === 1
      ? this._renderHomeAssistantOneCalendar()
      : this._renderHomeAssistantMultipleCalendars();
  }

  private _renderHomeAssistantNoCalendar() {
    return html`
      <ha-alert alert-type="error"> ${tp('homeassistant.error')} </ha-alert>
      <mwc-button @click=${this._back} slot="secondaryAction">
        &lt; ${this.hass.localize('ui.common.back')}
      </mwc-button>
    `;
  }

  private _renderHomeAssistantOneCalendar() {
    // Assign only choice without asking user
    const entity = this._homeAssistantCalendars[0];
    this._integrationCalendarEntityName = entity.entity_id;

    return html`
      <strong>Calendar name</strong>
      <div>${entity.attributes.friendly_name}</div>
      <mwc-button @click=${this._back} slot="secondaryAction">
        &lt; ${this.hass.localize('ui.common.back')}
      </mwc-button>
      <mwc-button @click=${this._save} slot="primaryAction">
        ${this.hass.localize('ui.common.save')}
      </mwc-button>
    `;
  }

  private _renderHomeAssistantMultipleCalendars() {
    const description = tp('homeassistant.description');
    const integrationCalendarEntityNameState =
      this.hass.states[entityIds.integrationCalendarEntityName];
    const current = this._homeAssistantCalendars.find(
      entity => entity.entity_id === this._integrationCalendarEntityName
    );
    const callback = evt => {
      const selected = this._homeAssistantCalendars.find(
        entity => entity.attributes.friendly_name === evt.target.value
      );
      this._integrationCalendarEntityName = selected.entity_id;
    };
    const options = this._homeAssistantCalendars.map(
      entity => entity.attributes.friendly_name
    );

    return html`
      <ha-markdown breaks .content=${description}></ha-markdown>
      ${renderInputSelect(
        current?.attributes.friendly_name,
        integrationCalendarEntityNameState,
        callback,
        options
      )}
      <mwc-button @click=${this._back} slot="secondaryAction">
        &lt; ${this.hass.localize('ui.common.back')}
      </mwc-button>
      <mwc-button @click=${this._save} slot="primaryAction">
        ${this.hass.localize('ui.common.save')}
      </mwc-button>
    `;
  }

  private _back(): void {
    this._caldavConnectionStatus = '';
    this._currentPage = 'source-selection';
  }

  private _continue(): void {
    if (this._carCalendarSource === 'Direct caldav source') {
      this._currentPage = 'caldav-calendar';
    } else {
      this._homeAssistantCalendars = this._getHomeAssistantCalendars();
      this._currentPage = 'homeassistant-calendar';
    }
  }

  private _getHomeAssistantCalendars(): HassEntity[] {
    const calendars = Object.keys(this.hass.states)
      .filter(entityId => /^calendar\./.test(entityId))
      .map(entityId => this.hass.states[entityId]);
    return calendars;
  }

  private async _continueCaldav(): Promise<void> {
    this._hasTriedToConnectToCaldav = true;
    // Todo: add validation
    try {
      this._caldavConnectionStatus = CaldavConnectionStatus.Connecting;
      const result = await callFunction(
        this.hass,
        'test_caldav_connection',
        {
          url: this._calendarAccountUrl,
          username: this._calendarAccountUsername,
          password: this._calendarAccountPassword,
        },
        10 * 1000
      );
      this._caldavConnectionStatus = result.msg;
      if (this._isConnectedToCaldav()) {
        this._caldavCalendars = result.calendars;
      }
    } catch (err) {
      this._caldavConnectionStatus = CaldavConnectionStatus.TimedOut;
    }
  }

  private async _save(): Promise<void> {
    const isUsingCalDav = this._carCalendarSource === 'Direct caldav source';
    const args = {
      source: this._carCalendarSource,
      ...(isUsingCalDav
        ? {
            url: this._calendarAccountUrl,
            username: this._calendarAccountUsername,
            password: this._calendarAccountPassword,
            calendar: this._carCalendarName,
          }
        : {
            calendar: this._integrationCalendarEntityName,
          }),
    };
    const result = await callFunction(
      this.hass,
      'save_calendar_settings',
      args
    );
    this.closeDialog();
  }

  static styles = css`
    .select-name {
      font-weight: bold;
    }

    .success ha-svg-icon {
      color: var(--success-color);
      padding-right: 2rem;
    }

    .success {
      margin-bottom: 2rem;
      font-size: 1.2rem;
    }
  `;
}
