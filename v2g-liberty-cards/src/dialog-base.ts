import { LitElement } from 'lit';
import { property, state } from 'lit/decorators';
import { HomeAssistant, fireEvent } from 'custom-card-helpers';
import { HassEntity } from 'home-assistant-js-websocket';

export function defaultState(
  stateObj: HassEntity,
  defaultValue: string
): string {
  return isUninitialised(stateObj) ? defaultValue : stateObj.state;
}

function isUninitialised(stateObj: HassEntity): boolean {
  return stateObj.state === 'unknown';
}

export abstract class DialogBase extends LitElement {
  @property({ attribute: false }) public hass!: HomeAssistant;

  @state() protected isOpen: boolean;

  public async showDialog(): Promise<void> {
    this.isOpen = true;
  }

  public closeDialog(): void {
    this.isOpen = false;
    fireEvent(this, 'dialog-closed', { dialog: this.localName });
  }
}
