import { mdiClose, mdiPencil } from '@mdi/js';
import { HomeAssistant } from 'custom-card-helpers';
import { HassEntity } from 'home-assistant-js-websocket';
import { html, nothing, TemplateResult } from 'lit';

import { t, to, partial } from '../util/translate';

export function renderLoadbalancerInfo(loadbalancerEnabled: boolean) {
  const tp = partial('settings.charger');
  const title = loadbalancerEnabled
    ? tp('load-balancer.enabled.title')
    : tp('load-balancer.not_enabled.title');
  const info = loadbalancerEnabled
    ? tp('load-balancer.enabled.info')
    : tp('load-balancer.not_enabled.info');
  const type = loadbalancerEnabled ? "info" : "warning";

  return html`
    <ha-alert title="${title}" alert-type="${type}">
      <ha-markdown breaks .content=${info}></ha-markdown>
    </ha-alert>
  `;
}

export function isLoadbalancerEnabled(quasarLoadBalancerLimit: string): boolean {
  return !isNaN(parseInt(quasarLoadBalancerLimit, 10));
}

export function renderButton(
  hass: HomeAssistant,
  action: (() => void),
  isPrimaryAction: boolean = true,
  label: string = null,
  isDisabled: boolean = false,
  testId: string = null,
  isBackButton: boolean = false
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
  const appearance = isPrimaryAction
    ? 'filled'
    : 'outlined'
  const variant = isPrimaryAction
    ? 'brand'
    : 'secondary'
  const chevronIcon = isBackButton
    ? html`<ha-icon icon="mdi:chevron-left" slot="start"></ha-icon>`
    : nothing;

  if (testId === null) {
    testId = isPrimaryAction
      ? 'continue'
      : 'previous'
  }

  return html`
    <ha-button @click=${action} slot=${slot} appearance=${appearance} variant=${variant} test-id=${testId} .disabled=${isDisabled} size='small'>
      ${chevronIcon}
      ${label}
    </ha-button>
  `;
}

export function renderSpinner() {
  return html`
    <ha-spinner
      test-id="progress"
      size="small"
      slot='primaryAction'
    ></ha-spinner>
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
        style="margin-left: auto;"
      ></ha-icon-button>
    </div>
  `;
  // Style 'margin-left' is added to repair flexbox behaviour and make button right-align.
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
  pattern: string = '^[0-9\\.]+$'
): TemplateResult {
  const name = t(stateObj.entity_id) || stateObj.attributes.friendly_name;
  return html`
    <ha-settings-row>
      <span slot="heading">
        <ha-icon .icon="${stateObj.attributes.icon}"></ha-icon>
        ${name}
      </span>
      <ha-textfield
        .pattern=${pattern}
        id="inputField"
        .step=${Number(stateObj.attributes.step)}
        .min=${Number(stateObj.attributes.min)}
        .max=${Number(stateObj.attributes.max)}
        .value=${Number(value).toString()}
        .suffix=${stateObj.attributes.unit_of_measurement || ''}
        type="number"
        inputmode="numeric"
        no-spinner
        @change=${changedCallback}
        test-id="${stateObj.entity_id}"
      >
      </ha-textfield>
    </ha-settings-row>
  `;
}

export enum InputText {
  // TODO: Fix these patterns and use the correct ones, see:
  // https://regex101.com/r/YlNtZS/1  and  https://extendsclass.com/regex-tester.html
  // An extra \ is needed as the parsing takes out one already
  EMail = '^[\\w.%+\\-]+@[a-zA-Z0-9.\\-]+\\.[a-zA-Z]{2,6}$',
  // Among others tested for 'name.surname@gmail.com', '1-2@seita.energy'
  EntityId = '^[\\w_]+\\.[\\d\\w_]+$',
  IpAddress = '^(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\\.(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\\.(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\\.(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$',
  Hostname = "^[\\w.\\-]+$",
  // Allows word characters (letters, digits, underscore), dots, and hyphens. Rejects spaces and special chars. Actual validity checked by connection test.
  OctopusCode = '^[\\w\\d-]+$',
  URL = '^https?:\\/\\/[\\w@:%.\\+\\-~#=]{1,256}\\b([a-zA-Z0-9\\(\\)@:%_\\+\\-.~#?&\\/=]*)$',
  // Among others tested for: 'http://localhost:1234', 'https://www.icloud.com/dav', 'https://ems.seita.energy/'
  UserName = '^[\\w\\-.@]{2,}$',
  Password = '.{4,}',
}

export function renderInputText(
  pattern: InputText,
  value: string,
  stateObj: HassEntity,
  changedCallback,
  validationMessage: string = "",
  type: string = "text"
): TemplateResult {
  const name = t(stateObj.entity_id) || stateObj.attributes.friendly_name;
  // Not happy with fixed height but can't get helper text error to render correctly.
  if (validationMessage === "") {
    // Use generic fallback validation message
    const tp = partial('settings.common');
    validationMessage = tp("validation_error");
  }

  return html`
    <ha-settings-row style="height: 85px;">
      <span slot="heading">
        <ha-icon .icon="${stateObj.attributes.icon}"></ha-icon>
      </span>
      <ha-textfield
        type="${type}"
        required="required"
        .autovalidate=${pattern}
        .pattern=${pattern}
        .validationMessage=${validationMessage}
        .label=${name}
        .value=${value}
        @change=${changedCallback}
        test-id="${stateObj.entity_id}"
        style="width: 100%"
      ></ha-textfield
    ></ha-settings-row>
  `;
}