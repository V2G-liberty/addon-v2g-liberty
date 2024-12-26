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

  ha-icon-button {
    color: var(--primary-color);
  }

  ha-settings-row {
    padding: 0;
  }

  div.button-row {
    margin-top:16px;
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

  ha-settings-row span[slot="heading"] ha-icon {
    color: var(--paper-item-icon-color, #44739e);
  }

  div.button-row {
    margin-top:16px;
  }


`;
