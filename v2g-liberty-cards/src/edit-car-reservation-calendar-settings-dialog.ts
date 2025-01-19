import { mdiChevronLeft } from '@mdi/js';
import { css, html, nothing } from 'lit';
import { customElement, state } from 'lit/decorators';
import { HassEntity } from 'home-assistant-js-websocket';
import { styles } from './card.styles';
import { callFunction } from './util/appdaemon';
import {
  InputText,
  renderDialogHeader,
  renderButton,
  renderSpinner,
  renderInputSelect,
  renderInputText,
  renderSelectOptionWithLabel,
} from './util/render';
import { partial } from './util/translate';
import { defaultState, DialogBase } from './dialog-base';
import * as entityIds from './entity-ids';

export const tagName = 'edit-car-reservation-calendar-settings-dialog';
const tp = partial('settings.car-reservation-calendar');

const enum CaldavConnectionStatus {
  Connected = 'Successfully connected',
  Connecting = 'Trying to connect...',
  Failed = 'Failed to connect',
  TimedOut = 'Timed out',
}
type CalendarSourceType = 'remoteCaldav' | 'localIntegration' | null;
const ValidCalendarSourceTypes: CalendarSourceType[] = ['remoteCaldav', 'localIntegration'];

@customElement(tagName)
class EditCarReservationCalendarSettingsDialog extends DialogBase {
  @state() private _currentPage: string;
  @state() private _calendarSourceType: CalendarSourceType;
  @state() private _calendarAccountUrl: string;
  @state() private _calendarAccountUsername: string;
  @state() private _calendarAccountPassword: string;
  @state() private _carCalendarName: string;
  @state() private _integrationCalendarEntityName: string;
  @state() private _caldavConnectionStatus: string;
  @state() private _hasSelectedCalenderProvider: boolean;

  private _caldavCalendars: string[];
  private _homeAssistantCalendars: HassEntity[];
  private _hasTriedToConnectToCaldav: boolean;

  public async showDialog(): Promise<void> {
    super.showDialog();
    this._currentPage = 'source-selection';
    this._setCalendarSourceType(this.hass.states[entityIds.carCalendarSource].state);
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
    this._hasSelectedCalenderProvider = false;
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

  private _setCalendarSourceType(newSource: string) {
    if (!newSource) {
      this._calendarSourceType = null;
      return;
    }
    if (this._isValidCalendarSourceType(newSource)) {
      this._calendarSourceType = newSource;
    } else {
      this._calendarSourceType = null;
    }
  }

  private _isValidCalendarSourceType(value: string): value is CalendarSourceType {
    return ValidCalendarSourceTypes.includes(value as CalendarSourceType);
  }

  private _renderCarCalendarSourceSelection() {
    const description = tp('source-selection.description');
    const selectName = tp('source-selection.select-name');
    const noSelectionError = tp('source-selection.no-selection-error')
    const current = this._calendarSourceType;
    const changedCallback = evt => this._setCalendarSourceType(evt.target.value);
    const isSelectionValid = this._isValidCalendarSourceType(current);

    return html`
      <p>${description}</p>
      <div class="select-options">
      <p class="select-name">${selectName}</p>
      ${!isSelectionValid && this._hasSelectedCalenderProvider
          ? this._renderError(noSelectionError)
          : nothing
        }
      ${ValidCalendarSourceTypes.map(option => {
        const label = html`
            <b>${tp(`source-selection.${option}-title`)}</b>
            <div class="option-description">
              ${tp(`source-selection.${option}-description`)}
            </div>
        `;
        return html`
          <p>
            ${renderSelectOptionWithLabel(
              option,
              label,
              option === current,
              changedCallback
            )}
          </p>
        `;
      })}
      </div>
      ${renderButton(this.hass, this._continue, true)}
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
    const urlError = tp('caldav.url-error');
    const usernameError = tp('caldav.username-error');
    const passwordError = tp('caldav.password-error');
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
        evt => (this._calendarAccountUrl = evt.target.value),
        urlError,
        "url"
      )}
      ${renderInputText(
        InputText.UserName,
        this._calendarAccountUsername,
        calendarAccountUsernameState,
        evt => (this._calendarAccountUsername = evt.target.value),
        usernameError
      )}
      ${renderInputText(
        InputText.Password,
        this._calendarAccountPassword,
        calendarAccountPasswordState,
        evt => (this._calendarAccountPassword = evt.target.value),
        passwordError,
        "password"
      )}
      ${this._renderConnectionError()}
      ${renderButton(this.hass, this._backToSourceSelection, false)}
      ${this._isBusyConnecting()
        ? renderSpinner()
        : renderButton(this.hass, this._continueCaldav)
      }
    `;
  }

  private _renderError(errorString: string) {
    return errorString
      ? html` <div class="error">${errorString}</div> `
      : nothing;
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
      ${renderButton(this.hass, this._backToSourceSelection, false)}
    `;
  }
  private _renderLoginSuccessful() {
    return html`
      <ha-alert alert-type="success">
        ${tp('caldav.login-success')}
      </ha-alert>
    `;
  }

  private _renderCaldavOneCalendar() {
    // Assign the only possible choice without asking user.
    this._carCalendarName = this._caldavCalendars[0];

    return html`
      ${this._renderLoginSuccessful()}
      <strong>Calendar name</strong>
      <div>${this._carCalendarName}</div>

      ${renderButton(this.hass, this._backToSourceSelection, false)}
      ${renderButton(this.hass, this._save, true, this.hass.localize('ui.common.save'))}
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
        carCalendarNameState,
        evt => (this._carCalendarName = evt.target.value),
        this._caldavCalendars
    )}
      ${renderButton(this.hass, this._backToSourceSelection, false)}
      ${renderButton(this.hass, this._save, true, this.hass.localize('ui.common.save'))}
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
      ${renderButton(this.hass, this._backToSourceSelection, false)}
    `;
  }

  private _renderHomeAssistantOneCalendar() {
    // Assign only choice without asking user
    const entity = this._homeAssistantCalendars[0];
    this._integrationCalendarEntityName = entity.entity_id;

    return html`
      <strong>Calendar name</strong>
      <div>${entity.attributes.friendly_name}</div>
      ${renderButton(this.hass, this._backToSourceSelection, false)}
      ${renderButton(this.hass, this._save, true, this.hass.localize('ui.common.save'))}
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
      ${renderButton(this.hass, this._backToSourceSelection, false)}
      ${renderButton(this.hass, this._save, true, this.hass.localize('ui.common.save'))}
    `;
  }

  private _backToSourceSelection(): void {
    this._caldavConnectionStatus = '';
    this._currentPage = 'source-selection';
  }

  private _continue(): void {
    this._hasSelectedCalenderProvider = true;
    if (this._calendarSourceType === 'remoteCaldav') {
      this._currentPage = 'caldav-calendar';
    } else if (this._calendarSourceType === 'localIntegration') {
      this._homeAssistantCalendars = this._getHomeAssistantCalendars();
      this._currentPage = 'homeassistant-calendar';
    } else {
      this._currentPage = 'source-selection';
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
    const isUsingCalDav = this._calendarSourceType === 'remoteCaldav';
    const args = {
      source: this._calendarSourceType,
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

  static styles = [
    styles,
    css`
      // Unfortunately this does not work
      // .mdc-text-field-helper-text--validation-msg {
      //   margin-bottom: 20px !important;
      // }
      `
  ];
}
