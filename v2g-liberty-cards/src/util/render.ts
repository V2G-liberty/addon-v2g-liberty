import { mdiClose } from '@mdi/js';
import { HomeAssistant } from 'custom-card-helpers';
import { HassEntity } from 'home-assistant-js-websocket';
import { html, TemplateResult } from 'lit';

import { to } from '../util/translate';

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
  stateObj: HassEntity,
  changedCallback
): TemplateResult {
  const isOn = stateObj.state === 'on';
  return html`
    <ha-settings-row>
      <span slot="heading">
        <ha-icon .icon="${stateObj.attributes.icon}"></ha-icon>
        ${stateObj.attributes.friendly_name}</span
      >
      <ha-switch
        test-id="${stateObj.entity_id}"
        .checked=${isOn}
        @change=${changedCallback}
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
  const groupName = stateObj.entity_id;
  return html`
    <div>
      <span class="select-name">${stateObj.attributes.friendly_name}</span>
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
  return html`
    <ha-settings-row>
      <span slot="heading">
        <ha-icon .icon="${stateObj.attributes.icon}"></ha-icon>
        ${stateObj.attributes.friendly_name}</span
      >
      <ha-textfield
        test-id="${stateObj.entity_id}"
        pattern="${pattern}"
        id="inputField"
        .step=${Number(stateObj.attributes.step)}
        .min=${Number(stateObj.attributes.min)}
        .max=${Number(stateObj.attributes.max)}
        .value=${Number(value).toString()}
        .suffix=${stateObj.attributes.unit_of_measurement || ''}
        type="number"
        @change=${changedCallback}
      >
      </ha-textfield>
    </ha-settings-row>
  `;
}

export function renderInputText(
  value: string,
  stateObj: HassEntity,
  changedCallback
): TemplateResult {
  return html`
    <ha-settings-row>
      <span slot="heading">
        <ha-icon .icon="${stateObj.attributes.icon}"></ha-icon>
        ${stateObj.attributes.friendly_name}</span
      >
      <ha-textfield
        pattern="[\\w_]+\\.[\\d\\w_]+"
        .value=${value}
        @change=${changedCallback}
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
  return html`
    <ha-settings-row>
      <span slot="heading">
        <ha-icon .icon="${stateObj.attributes.icon}"></ha-icon>
        ${stateObj.attributes.friendly_name}</span
      >
      <ha-textfield type="password" .value=${value} @change=${changedCallback}>
      </ha-textfield
    ></ha-settings-row>
  `;
}
