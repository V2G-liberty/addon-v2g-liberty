import { css } from 'lit';

export const styles = css`
  .description {
    // padding-bottom: 2em;
  }
  .value {
    display: flex;
    align-content: center;
    flex-wrap: wrap;
  }

  .success ha-svg-icon {
    color: var(--success-color) !important;
  }

  .success {
    margin: 4px 0 12px 0;
    text-align: center;
    padding: 10px 12px 8px 4px;
    border: 1px solid color-mix(in srgb, var(--success-color) 50%, transparent);
    border-radius: 4px;
  }

  ha-icon-button {
    color: color-mix(in srgb, var(--primary-color) 55%, transparent);
  }
  ha-icon-button:hover {
    color: var(--primary-color);
  }

  ha-settings-row {
    padding: 0;
  }

  ha-settings-row {
    overflow: hidden !important;
    height: 64px;
  }

  ha-settings-row div.state {
    font-weight: 500;
    margin-right: 4px;
  }

  ha-settings-row span[slot="heading"] {
    margin-top: 6px !important;
  }

  ha-settings-row span[slot="heading"] ha-icon, ha-icon {
    color: var(--paper-item-icon-color, #44739e);
  }

  div.button-row {
    margin-top:16px;
  }

  .error {
    color: var(--error-color);
  }

  ha-dialog {
    --mdc-dialog-width: 40%;
    --mdc-dialog-max-width: 680px;
    --mdc-dialog-min-width: 400px;
    // --mdc-dialog-padding: 8px !important;
  }

  // The padding stuff sdoes not work this way, the element is not reached with this css selectors.
  // ha-dialog::part(surface), ha-dialog div.mdc-dialog__content div.mdc-dialog__surface, .mdc-dialog__surface {
  //   padding: 8px !important;
  // }

  ha-dialog ha-markdown {
    margin-top: 16px;
  }

  `;
