import { mdiClose, mdiPencil, mdiChevronLeft } from '@mdi/js';
import { HomeAssistant } from 'custom-card-helpers';
import { HassEntity } from 'home-assistant-js-websocket';
import { html, nothing, TemplateResult } from 'lit';

import { t, to } from '../util/translate';

export function renderButton(
  hass: HomeAssistant,
  action: (() => void),
  isPrimaryAction: boolean = true,
  label: string = null,
) {
  if (label === null) {
    if (isPrimaryAction) {
      label = hass.localize('ui.common.continue')
    } else {
      label = hass.localize('ui.common.back');
    }
  }
  const slot = isPrimaryAction
    ? 'primaryAction'
    : 'secondaryAction'
  const chevronIcon = !isPrimaryAction
    ? html`<ha-svg-icon .path=${mdiChevronLeft}></ha-svg-icon> `
    : nothing;

  return html`
    <mwc-button @click=${action} slot=${slot}>
      ${chevronIcon}${label}
    </mwc-button>
  `;
}

export function renderSpinner() {
  // To replace primaryAction button while busy
  return html`
    <ha-circular-progress
      size="small"
      indeterminate
      slot='primaryAction'
    ></ha-circular-progress>
  `;
}

export function renderEntityBlock(
  stateObj: HassEntity,
  { state }: { state?: string } = {}
) {
  state = state || to(stateObj.state) || stateObj.state;
  const name = t(stateObj.entity_id) || stateObj.attributes.friendly_name;
  return html`
    <ha-settings-row>
      <span slot="heading" test-id="${stateObj.entity_id}">
        <ha-icon .icon=${stateObj.attributes.icon}></ha-icon>
        <span style="display:inline-block"
          ><span
            style="font-size: var(--mdc-typography-body2-font-size, 0.875rem); color: var(--secondary-text-color)"
            >${name}</span
          >
          <div>${state}</div></span
        >
      </span>
    </ha-settings-row>
  `;
}

export function renderEntityRow(
  stateObj: HassEntity,
  { callback, state }: { callback?: any; state?: string } = {}
) {
  state = state || to(stateObj.state) || stateObj.state;
  const name = t(stateObj.entity_id) || stateObj.attributes.friendly_name;
  return html`
    <ha-settings-row>
      <span slot="heading">
        <ha-icon .icon=${stateObj.attributes.icon}></ha-icon>&nbsp; &nbsp;
        ${name}
      </span>
      <div class="text-content value state">${state}</div>
      ${callback
        ? html`
            <ha-icon-button
              .path=${mdiPencil}
              @click=${callback}
            ></ha-icon-button>
          `
        : nothing}
    </ha-settings-row>
  `;
}

export function renderDialogHeader(
  hass: HomeAssistant,
  title: string
): TemplateResult {
  return html`
    <div class="header_title">
      <span>${title}</span>
      <ha-icon-button
        .label=${hass.localize('ui.dialogs.generic.close') ?? 'Close'}
        .path="${mdiClose}"
        dialogAction="close"
        class="header_button"
      ></ha-icon-button>
    </div>
  `;
}

export function renderInputBoolean(
  isOn: boolean,
  stateObj: HassEntity,
  changedCallback
): TemplateResult {
  const name = t(stateObj.entity_id) || stateObj.attributes.friendly_name;
  return html`
    <ha-settings-row>
      <span slot="heading">
        <ha-icon .icon="${stateObj.attributes.icon}"></ha-icon>
        ${name}
      </span>
      <ha-switch
        .checked=${isOn}
        @change=${changedCallback}
        test-id="${stateObj.entity_id}"
      ></ha-switch>
    </ha-settings-row>
  `;
}

export function renderSelectOption(
  option: string,
  isChecked: boolean,
  changedCallback,
  group: string = null
): TemplateResult {
  let label = to(option) || option;
  return renderSelectOptionWithLabel(
    option,
    label,
    isChecked,
    changedCallback,
    group
  );
}

export function renderSelectOptionWithLabel(
  option: string,
  label: string | TemplateResult,
  isChecked: boolean,
  changedCallback,
  group: string = null
): TemplateResult {
  return html`
    <div>
      <ha-formfield .label=${label}>
        <ha-radio
          .checked=${isChecked}
          .value=${option}
          .name=${group}
          @change=${changedCallback}
        ></ha-radio>
      </ha-formfield>
    </div>
  `;
}

export function renderInputSelect(
  currentValue: string,
  stateObj: HassEntity,
  changedCallback,
  options?: string[]
): TemplateResult {
  options = options ?? stateObj.attributes.options;
  const name = t(stateObj.entity_id) || stateObj.attributes.friendly_name;
  const groupName = stateObj.entity_id;
  return html`
    <div>
      <span class="select-name">${name}</span>
      <ha-icon .icon="${stateObj.attributes.icon}"></ha-icon>
    </div>
    <div class="select-options">
      ${options.map(option =>
        renderSelectOption(
          option,
          option === currentValue,
          changedCallback,
          groupName
        )
      )}
    </div>
  `;
}

export function renderInputNumber(
  value: string,
  stateObj: HassEntity,
  changedCallback,
  pattern: string = '[0-9\\.]+'
): TemplateResult {
  const name = t(stateObj.entity_id) || stateObj.attributes.friendly_name;
  return html`
    <ha-settings-row>
      <span slot="heading">
        <ha-icon .icon="${stateObj.attributes.icon}"></ha-icon>
        ${name}
      </span>
      <ha-textfield
        pattern="${pattern}"
        id="inputField"
        .step=${Number(stateObj.attributes.step)}
        .min=${Number(stateObj.attributes.min)}
        .max=${Number(stateObj.attributes.max)}
        .value=${Number(value).toString()}
        .suffix=${stateObj.attributes.unit_of_measurement || ''}
        type="number"
        @change=${changedCallback}
        test-id="${stateObj.entity_id}"
      >
      </ha-textfield>
    </ha-settings-row>
  `;
}

export enum InputText { // TODO: Fix these patterns and use the correct ones
  EMail = '^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$',
  EntityId = '[\\w_]+\\.[\\d\\w_]+',
  IpAddress = '^(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$',
  OctopusCode = '[\\w\\d-]+',
  URL = '^https?:\/\/(www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b([-a-zA-Z0-9()@:%_\+.~#?&//=]*)$',
  UserName = '^[a-zA-Z0-9.@]{2,}$',
  PassWord = '' //'^.{6,}$',
}

export function renderInputText(
  pattern: InputText,
  value: string,
  stateObj: HassEntity,
  changedCallback
): TemplateResult {
  const name = t(stateObj.entity_id) || stateObj.attributes.friendly_name;
  return html`
    <ha-settings-row>
      <span slot="heading">
        <ha-icon .icon="${stateObj.attributes.icon}"></ha-icon>
      </span>
      <ha-textfield
        requiered="true"
        pattern="${pattern}"
        .label=${name}
        .value=${value}
        @change=${changedCallback}
        test-id="${stateObj.entity_id}"
        style="width: 100%"
      >
      </ha-textfield
    ></ha-settings-row>
  `;
}

export function renderInputPassword(
  value: string,
  stateObj: HassEntity,
  changedCallback
): TemplateResult {
  const name = t(stateObj.entity_id) || stateObj.attributes.friendly_name;
  return html`
    <ha-settings-row>
      <span slot="heading">
        <ha-icon .icon="${stateObj.attributes.icon}"></ha-icon>
      </span>
      <ha-textfield
        requiered="true"
        type="password"
        .label=${name}
        .value=${value}
        @change=${changedCallback}
        test-id="${stateObj.entity_id}"
        style="width: 100%"
      >
      </ha-textfield
    ></ha-settings-row>
  `;
}
