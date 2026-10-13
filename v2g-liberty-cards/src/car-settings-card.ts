import { css, html, LitElement, nothing } from 'lit';
import { customElement, state } from 'lit/decorators';
import { HassEvent } from 'home-assistant-js-websocket';
import { HomeAssistant, LovelaceCardConfig } from 'custom-card-helpers';

import {
  renderButton,
  renderLoadFailedCard,
  renderSettingsRow,
} from './util/render';
import { partial, setLanguage } from './util/translate';
import { callFunction, SETTINGS_LOAD_TIMEOUT_MS } from './util/appdaemon';
import { styles } from './card.styles';
import { showCarSettingsDialog } from './show-dialogs';
import { CarField, CAR_DETAIL_FIELDS, CarSettings } from './car-fields';
import * as entityIds from './entity-ids';

const tp = partial('settings.car');
const tc = partial('settings.common');

@customElement('v2g-liberty-car-settings-card')
class CarSettingsCard extends LitElement {
  @state() private _settings: CarSettings | null = null;
  @state() private _loading = true;
  @state() private _loadFailed = false;

  private _hass: HomeAssistant;
  private _unsubCar: (() => void) | null = null;
  private _unsubCharger: (() => void) | null = null;
  private _rebootedAt: string | null = null;

  setConfig(config: LovelaceCardConfig) {}

  set hass(hass: HomeAssistant) {
    const firstSet = !this._hass;
    this._hass = hass;
    setLanguage(hass.locale?.language ?? (hass as any).language);
    if (firstSet) {
      this._rebootedAt = hass.states[entityIds.appRebootedAt]?.state ?? null;
      this._loadSettings();
      this._subscribe();
      return;
    }
    // The add-on stamps this at the end of every start-up. Reloading on a
    // change covers the two cases where the first attempt could not be
    // answered: the page was open (or refreshed) while the add-on was still
    // starting, and the add-on restarted afterwards.
    const rebootedAt = hass.states[entityIds.appRebootedAt]?.state ?? null;
    if (rebootedAt && rebootedAt !== this._rebootedAt) {
      this._rebootedAt = rebootedAt;
      this._loadSettings();
    }
  }

  static styles = [
    styles,
    css`
      /* A note under its row, not a second line inside it. Inside the row the
         value ends up centred against both lines, and that is the component's
         own doing (its default slot sits in a shadow element that is centred),
         so it cannot be fixed from out here. As a note the row stays one line,
         the value lines up with the label, and this sits underneath it.
         The indent matches the icon plus the gap the heading uses, so it
         starts where the label starts; the negative top margin pulls it back
         up against the row's own bottom padding. */
      .row-note {
        margin: -16px 0 4px 36px;
        font-size: 0.875em;
        color: var(--secondary-text-color);
      }
      /* ha-card pads its header 20px 16px 24px; with a subtitle underneath
         the bottom padding moves to the subtitle so the two read as one. */
      .card-header.has-subtitle {
        padding-bottom: 0;
      }
      .card-subtitle {
        padding: 0 var(--ha-space-4, 16px) var(--ha-space-4, 16px);
        font-size: 0.875em;
        color: var(--secondary-text-color);
      }
    `,
  ];

  private async _loadSettings() {
    this._loading = true;
    try {
      this._settings = (await callFunction(
        this._hass,
        'get_car_settings',
        {},
        SETTINGS_LOAD_TIMEOUT_MS
      )) as CarSettings;
      this._loadFailed = false;
    } catch (e) {
      // The backend answers this one even without a car, so a failure means
      // the add-on could not be reached -- not that nothing is configured.
      // Saying so is the point: drawing the configured card without any
      // values leaves an empty card, and drawing the unconfigured one invites
      // the user to set up a car that is already set up.
      console.error('Failed to load car settings', e);
      this._settings = null;
      this._loadFailed = true;
    }
    this._loading = false;
  }

  private async _subscribe() {
    this._unsubCar = await this._hass.connection.subscribeEvents<HassEvent>(
      () => this._loadSettings(),
      'save_car_settings.result'
    );
    // Whether the charger identifies cars decides if the car ID is shown and
    // required, so a charger save can change what this card has to say.
    this._unsubCharger = await this._hass.connection.subscribeEvents<HassEvent>(
      () => this._loadSettings(),
      'save_charger_settings.result'
    );
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    if (this._unsubCar) {
      this._unsubCar();
      this._unsubCar = null;
    }
    if (this._unsubCharger) {
      this._unsubCharger();
      this._unsubCharger = null;
    }
  }

  render() {
    if (this._loading) {
      return html`<ha-card header=${tp('header')}>
        <div class="card-content"><ha-spinner></ha-spinner></div>
      </ha-card>`;
    }
    if (this._loadFailed)
      return renderLoadFailedCard(this._hass, tp('header'), () =>
        this._loadSettings()
      );
    // The flag is derived by the backend (a configured car without an ID is
    // not finished on a charger that identifies cars) and published as a
    // runtime sensor, which the blocking settings dialog reads too. Taking it
    // from there keeps card and dialog from ever disagreeing.
    const isInitialised =
      this._hass.states[entityIds.carSettingsInitialised]?.state === 'on';
    // A third state, between "nothing set up" and "done": the car is fully
    // configured but sits on a charger that identifies cars and has no ID yet
    // -- a user who switched charger type. Only the ID is missing, so hiding
    // the values and asking them to configure a car they already configured
    // would be a lie.
    const needsId =
      !isInitialised &&
      !!this._settings?.configured &&
      !!this._settings?.identifies_car &&
      !this._settings?.ev_id;
    const header =
      ((isInitialised || needsId) && this._settings?.name) || tp('header');
    // The ID is not a setting the user edits but a fact about the car, so it
    // belongs with the name rather than in the list of values. ha-card has no
    // subtitle of its own; it does style a slotted .card-header exactly like
    // the header it renders itself, so the header is ours and the subtitle
    // sits directly under it.
    const evId = this._settings?.identifies_car ? this._settings?.ev_id : '';
    const subtitle =
      (isInitialised || needsId) && evId
        ? tp('car-id-subtitle', { id: evId })
        : null;
    const content = isInitialised
      ? this._renderInitialisedContent()
      : needsId
        ? this._renderInitialisedContent(true)
        : this._renderUninitialisedContent();
    return html`<ha-card>
      <h1 class="card-header ${subtitle ? 'has-subtitle' : ''}">${header}</h1>
      ${subtitle ? html`<div class="card-subtitle">${subtitle}</div>` : nothing}
      ${content}
    </ha-card>`;
  }

  private _renderUninitialisedContent() {
    // A charger that identifies cars needs the car's ID as well, and that can
    // only be read while the car is plugged in -- say so up front.
    const alert = this._settings?.identifies_car
      ? tp('alert-with-id')
      : tp('alert');
    return html`
      <div class="card-content">
        <ha-alert alert-type="warning" title=${tp('alert-title')}>
          ${alert}
        </ha-alert>
      </div>
      <div class="card-actions">
        ${renderButton(this._hass, () => this._openDialog(), true, tc('configure'))}
      </div>
    `;
  }

  private _renderInitialisedContent(needsId = false) {
    return html`
      <div class="card-content">
        ${needsId
          ? html`<ha-alert
              alert-type="warning"
              title=${tp('missing-id-title')}
            >
              ${tp('missing-id')}
            </ha-alert>`
          : nothing}
        ${CAR_DETAIL_FIELDS.map(field => this._renderField(field))}
        ${this._renderScheduleLimits()}
      </div>
      <div class="card-actions">
        ${renderButton(
          this._hass,
          () => this._openDialog(),
          true,
          this._hass.localize('ui.common.edit')
        )}
      </div>
    `;
  }

  /**
   * The lower and upper limit are one range, so one row says it better than
   * two -- and the card is a summary, not the settings themselves. The
   * allowed duration rides along as a second line: it only means anything in
   * relation to the upper limit.
   */
  private _renderScheduleLimits() {
    const low = this._settings?.min_soc_percent;
    const high = this._settings?.max_soc_percent;
    const hours = this._settings?.allowed_duration_above_max_soc_hrs;
    if (low === undefined || high === undefined) return nothing;
    return html`
      ${renderSettingsRow(
        'mdi:chart-bell-curve-cumulative',
        tp('fields.limits'),
        `${low} – ${high} %`
      )}
      ${hours === undefined
        ? nothing
        : html`<p class="row-note">
            ${tp('fields.limits-duration', { hours })}
          </p>`}
    `;
  }

  private _renderField(field: CarField) {
    const value = this._settings?.[field.key];
    if (value === undefined || value === null || value === '') return nothing;
    return renderSettingsRow(
      field.icon,
      tp(`fields.${field.label}`),
      `${value} ${field.suffix}`
    );
  }

  private _openDialog() {
    showCarSettingsDialog(this);
  }
}
