import { mdiClose, mdiPencil } from '@mdi/js';
import { HomeAssistant } from 'custom-card-helpers';
import { HassEntity } from 'home-assistant-js-websocket';
import { html, nothing, TemplateResult } from 'lit';
import { ifDefined } from 'lit/directives/if-defined';

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

// HA 2026.3 migrated ha-dialog from MDC (mwc-dialog) to WebAwesome (wa-dialog).
// The primaryAction/secondaryAction slots and TemplateResult .heading were removed.
// New API: slot="footer" for buttons, .headerTitle string property for the title.
export function isNewHaDialogAPI(hass: HomeAssistant): boolean {
  const [year, month] = hass.config.version.split('.').map(Number);
  return year > 2026 || (year === 2026 && month >= 3);
}

function _haDialogFooterSlot(hass: HomeAssistant): string {
  return isNewHaDialogAPI(hass) ? 'footer' : null;
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
  const footerSlot = _haDialogFooterSlot(hass);
  const slot = footerSlot ?? (isPrimaryAction ? 'primaryAction' : 'secondaryAction');
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
    <ha-button @click=${action} slot=${slot} appearance=${appearance} variant=${variant} test-id=${testId} .disabled=${isDisabled} size='small' style="width: auto">
      ${chevronIcon}
      ${label}
    </ha-button>
  `;
}

export function renderSpinner(hass: HomeAssistant = null) {
  if (hass && isNewHaDialogAPI(hass)) {
    // wa-dialog (HA ≥ 2026.3): render in content area, right-aligned to match button position.
    return html`
      <div style="display: flex; justify-content: flex-end;">
        <ha-spinner test-id="progress" size="small"></ha-spinner>
      </div>
    `;
  }
  return html`
    <ha-spinner test-id="progress" size="small" slot="primaryAction"></ha-spinner>
  `;
}

export function renderEntityBlock(
  hass: HomeAssistant,
  stateObj: HassEntity,
  { state }: { state?: string } = {}
) {
  state = state || to(stateObj.state) || stateObj.state;
  const name = t(stateObj.entity_id) || stateObj.attributes.friendly_name;
  if (isNewHaDialogAPI(hass)) {
    return html`
      <div test-id="${stateObj.entity_id}" style="display: flex; align-items: center; padding: 8px 0;">
        <ha-icon .icon=${stateObj.attributes.icon}></ha-icon>
        <span style="margin-left: 8px; display: inline-flex; flex-direction: column;">
          <span style="font-size: 0.875rem; color: var(--secondary-text-color);">${name}</span>
          <span>${state}</span>
        </span>
      </div>
    `;
  }
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

export interface HaInputOptions {
  value: string;
  onChange: (e: any) => void;
  label?: string;
  placeholder?: string;
  type?: string;
  inputmode?: string;
  required?: boolean;
  pattern?: string;
  min?: number | string;
  max?: number | string;
  step?: number | string;
  suffix?: string;
  id?: string;
  testId?: string;
  style?: string;
  noSpinButtons?: boolean;
}

// Single source of truth for our text/number inputs. Renders Home Assistant's
// current input element (ha-input, WebAwesome-based; ha-textfield was removed
// in HA 2026.5). Entity-free: callers pass plain values, not a HassEntity, so
// this works for both the legacy entity-backed settings and the newer
// entity-free dialogs (grid, solar, reset). Listens to value-changed/change/
// input so the callback fires regardless of which event ha-input emits.
export function renderHaInput(opts: HaInputOptions): TemplateResult {
  return html`
    <ha-input
      appearance="material"
      type=${opts.type ?? 'text'}
      inputmode=${ifDefined(opts.inputmode)}
      ?required=${!!opts.required}
      ?without-spin-buttons=${opts.noSpinButtons ?? opts.type === 'number'}
      pattern=${ifDefined(opts.pattern)}
      min=${ifDefined(opts.min)}
      max=${ifDefined(opts.max)}
      step=${ifDefined(opts.step)}
      placeholder=${ifDefined(opts.placeholder)}
      label=${ifDefined(opts.label)}
      id=${ifDefined(opts.id)}
      .value=${opts.value ?? ''}
      @value-changed=${opts.onChange}
      @change=${opts.onChange}
      @input=${opts.onChange}
      test-id=${ifDefined(opts.testId)}
      style=${ifDefined(opts.style)}
    >
      ${opts.suffix
        ? html`<span slot="end">${opts.suffix}</span>`
        : nothing}
    </ha-input>
  `;
}

export function renderInputNumber(
  value: string,
  stateObj: HassEntity,
  changedCallback,
  // pattern is kept for signature compatibility; ha-input validates via min/max.
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  pattern: string = '^[0-9\\.]+$'
): TemplateResult {
  const name = t(stateObj.entity_id) || stateObj.attributes.friendly_name;
  return html`
    <ha-settings-row>
      <span slot="heading">
        <ha-icon .icon="${stateObj.attributes.icon}"></ha-icon>
        ${name}
      </span>
      ${renderHaInput({
        value: Number(value).toString(),
        onChange: changedCallback,
        type: 'number',
        inputmode: 'numeric',
        noSpinButtons: true,
        min: stateObj.attributes.min,
        max: stateObj.attributes.max,
        step: stateObj.attributes.step,
        suffix: stateObj.attributes.unit_of_measurement || undefined,
        id: 'inputField',
        testId: stateObj.entity_id,
      })}
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
  type: string = "text",
  hass: HomeAssistant = null
): TemplateResult {
  const name = t(stateObj.entity_id) || stateObj.attributes.friendly_name;

  const textField = renderHaInput({
    value,
    onChange: changedCallback,
    label: name,
    type,
    required: true,
    pattern,
    testId: stateObj.entity_id,
    style: 'width: 100%',
  });

  if (hass && isNewHaDialogAPI(hass)) {
    return html`
      <div style="display: flex; align-items: flex-start; padding: 4px 0;">
        <ha-icon .icon="${stateObj.attributes.icon}" style="margin-right: 16px; flex-shrink: 0; margin-top: 16px;"></ha-icon>
        ${textField}
      </div>
    `;
  }

  return html`
    <ha-settings-row style="height: 85px;">
      <span slot="heading">
        <ha-icon .icon="${stateObj.attributes.icon}"></ha-icon>
      </span>
      ${textField}
    </ha-settings-row>
  `;
}