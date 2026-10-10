import { fireEvent } from 'custom-card-helpers';

import { tagName as editAdministratorSettingsDialogTag } from './edit-administrator-settings-dialog';
import { tagName as editCarReservationCalendarSettingsDialogTag } from './edit-car-reservation-calendar-settings-dialog';
import { tagName as editCarSettingsDialogTag } from './edit-car-settings-dialog';
import { tagName as editChargerSettingsDialogTag } from './edit-charger-settings-dialog';
import { tagName as editElectricityContractSettingsDialogTag } from './edit-electricity-contract-settings-dialog';
import { tagName as editScheduleSettingsDialogTag } from './edit-schedule-settings-dialog';
import { tagName as editInputNumberDialogTag } from './edit-inputnumer-dialog';
import { tagName as editInputSelectDialogTag } from './edit-inputselect-dialog';

import { tagName as editGridConnectionSettingsDialogTag } from './edit-grid-connection-settings-dialog';
import {
  tagName as chooseSensorDialogTag,
  ChooseSensorDialogParams,
} from './choose-sensor-dialog';
import {
  tagName as editSolarPanelDialogTag,
  SolarPanelDialogParams,
} from './edit-solar-panel-dialog';
import { tagName as showSettingsErrorAlertDialogTag } from './settings-error-alert-dialog';
import { tagName as resetDatabaseDialogTag } from './reset-database-dialog';


import { t, partial } from './util/translate';

const tp = partial('settings.dialogs');

// --- Reset Database ---

export const showResetDatabaseDialog = (element: HTMLElement): void => {
  fireEvent(element, 'show-dialog', {
    dialogTag: resetDatabaseDialogTag,
    dialogImport: () => Promise.resolve(),
    dialogParams: {},
  });
};

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

// One route to the car settings: both the Configure and the Edit button of the
// card open this dialog, which collects everything and saves once at the end.
// The six per-value dialogs it replaces are gone.
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

// --- Grid Connection Settings ---

export const showGridConnectionSettingsDialog = (
  element: HTMLElement
): void => {
  fireEvent(element, 'show-dialog', {
    dialogTag: editGridConnectionSettingsDialogTag,
    dialogImport: () => Promise.resolve(),
    dialogParams: {},
  });
};

// --- Choose sensor (side-step of the grid connection flow) ---

export const showChooseSensorDialog = (
  element: HTMLElement,
  dialogParams: ChooseSensorDialogParams
): void => {
  fireEvent(element, 'show-dialog', {
    dialogTag: chooseSensorDialogTag,
    dialogImport: () => Promise.resolve(),
    dialogParams,
  });
};

// --- Solar Panel (add/edit) ---

export const showSolarPanelDialog = (
  element: HTMLElement,
  params: SolarPanelDialogParams = {}
): void => {
  fireEvent(element, 'show-dialog', {
    dialogTag: editSolarPanelDialogTag,
    dialogImport: () => Promise.resolve(),
    dialogParams: params,
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
