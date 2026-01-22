import { fireEvent } from 'custom-card-helpers';

import { tagName as editAdministratorSettingsDialogTag } from './edit-administrator-settings-dialog';
import { tagName as editCarReservationCalendarSettingsDialogTag } from './edit-car-reservation-calendar-settings-dialog';
import { tagName as editCarSettingsDialogTag } from './edit-car-settings-dialog';
import { tagName as editChargerSettingsDialogTag } from './edit-charger-settings-dialog';
import { tagName as editElectricityContractSettingsDialogTag } from './edit-electricity-contract-settings-dialog';
import { tagName as editScheduleSettingsDialogTag } from './edit-schedule-settings-dialog';
import { tagName as editInputNumberDialogTag } from './edit-inputnumer-dialog';
import { tagName as editInputSelectDialogTag } from './edit-inputselect-dialog';

import { tagName as showSettingsErrorAlertDialogTag } from './settings-error-alert-dialog';


import { t, partial } from './util/translate';

const tp = partial('settings.dialogs');

// --- Show Settings Error Alert ---

export const showSettingsErrorAlertDialog = (element: HTMLElement): void => {
  fireEvent(element, 'show-dialog', {
    dialogTag: showSettingsErrorAlertDialogTag,
    dialogImport: () => Promise.resolve(),
    dialogParams: {},
  });
};


// --- Administrator Settings ---

export const showAdministratorSettingsDialog = (element: HTMLElement): void => {
  fireEvent(element, 'show-dialog', {
    dialogTag: editAdministratorSettingsDialogTag,
    dialogImport: () => Promise.resolve(),
    dialogParams: {},
  });
};

// --- Car Settings ---

export const showCarSettingsDialog = (element: HTMLElement): void => {
  fireEvent(element, 'show-dialog', {
    dialogTag: editCarSettingsDialogTag,
    dialogImport: () => Promise.resolve(),
    dialogParams: {},
  });
};

// --- Car reservation calendar settings ---

export const showCarReservationCalendarSettingsDialog = (
  element: HTMLElement
): void => {
  fireEvent(element, 'show-dialog', {
    dialogTag: editCarReservationCalendarSettingsDialogTag,
    dialogImport: () => Promise.resolve(),
    dialogParams: {},
  });
};

// --- Charger Settings ---

export const showChargerSettingsDialog = (element: HTMLElement): void => {
  fireEvent(element, 'show-dialog', {
    dialogTag: editChargerSettingsDialogTag,
    dialogImport: () => Promise.resolve(),
    dialogParams: {},
  });
};

// --- Electricity Contract Settings ---

export const showElectricityContractSettingsDialog = (
  element: HTMLElement
): void => {
  fireEvent(element, 'show-dialog', {
    dialogTag: editElectricityContractSettingsDialogTag,
    dialogImport: () => Promise.resolve(),
    dialogParams: {},
  });
};

// --- Optimisation Settings ---

export const showOptimisationModeDialog = (
  element: HTMLElement,
  dialogParams
): void => {
  showEditInputSelectDialog(element, {
    header: tp('optimisation-mode.header'),
    description: tp('optimisation-mode.description'),
    ...dialogParams,
  });
};

export const showCarBatteryLowerChargeLimitDialog = (
  element: HTMLElement,
  dialogParams
): void => {
  showEditInputNumberDialog(element, {
    header: tp('car-battery-lower-charge-limit.header'),
    description: tp('car-battery-lower-charge-limit.description'),
    ...dialogParams,
  });
};

export const showCarBatteryUpperChargeLimitDialog = (
  element: HTMLElement,
  dialogParams
): void => {
  showEditInputNumberDialog(element, {
    header: tp('car-battery-upper-charge-limit.header'),
    description: tp('car-battery-upper-charge-limit.description'),
    ...dialogParams,
  });
};

export const showAllowedDurationAboveMaxDialog = (
  element: HTMLElement,
  dialogParams
): void => {
  showEditInputNumberDialog(element, {
    header: tp('allowed-duration-above-max.header'),
    description: tp('allowed-duration-above-max.description'),
    ...dialogParams,
  });
};

// --- Schedule Settings ---

export const showScheduleSettingsDialog = (element: HTMLElement): void => {
  fireEvent(element, 'show-dialog', {
    dialogTag: editScheduleSettingsDialogTag,
    dialogImport: () => Promise.resolve(),
    dialogParams: {},
  });
};

// --- --- ---

export const showEditInputNumberDialog = (
  element: HTMLElement,
  dialogParams
): void => {
  fireEvent(element, 'show-dialog', {
    dialogTag: editInputNumberDialogTag,
    dialogImport: () => Promise.resolve(),
    dialogParams,
  });
};

export const showEditInputSelectDialog = (
  element: HTMLElement,
  dialogParams
): void => {
  fireEvent(element, 'show-dialog', {
    dialogTag: editInputSelectDialogTag,
    dialogImport: () => Promise.resolve(),
    dialogParams,
  });
};
