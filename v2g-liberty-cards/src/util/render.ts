import { mdiClose, mdiPencil } from '@mdi/js';
import { HomeAssistant } from 'custom-card-helpers';
import { HassEntity } from 'home-assistant-js-websocket';
import { html, nothing, TemplateResult } from 'lit';

import { t, to } from '../util/translate';

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
        ${state}
      </span>
      <span slot="description">${name}</span>
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
        <ha-icon .icon=${stateObj.attributes.icon}></ha-icon>
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
  EMail = '[\\w_]+\\.[\\d\\w_]+',
  EntityId = '[\\w_]+\\.[\\d\\w_]+',
  IpAddress = '[0-9\\.]+',
  OctopusCode = '[\\w\\d-]+',
  URL = '[\\w_]+\\.[\\d\\w_]+',
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
        ${name}
      </span>
      <ha-textfield
        pattern="${pattern}"
        .value=${value}
        @change=${changedCallback}
        test-id="${stateObj.entity_id}"
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
        ${name}
      </span>
      <ha-textfield
        type="password"
        .value=${value}
        @change=${changedCallback}
        test-id="${stateObj.entity_id}"
      >
      </ha-textfield
    ></ha-settings-row>
  `;
}
