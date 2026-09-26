import { css } from 'lit';

export const styles = css`
  .select-name {
    font-weigth: bold;
  }
  .value {
    display: flex;
    align-content: center;
    flex-wrap: wrap;
  }
  div.card-content ha-settings-row ha-icon-button {
    color: color-mix(in srgb, var(--primary-color) 65%, transparent) !important;
  }
  div.card-content ha-settings-row ha-icon-button:hover {
    color: var(--primary-color) !important;
  }

  /* Only the horizontal padding is ours: the card content already provides
     it, and HA's own would double it. Deliberately no height and no overflow
     rule -- ha-settings-row sizes itself from its content with a min-height
     (56px for two lines, 88px for three) and clips its children, not itself.
     Forcing 64px with the overflow hidden made every row taller than HA
     intends and cut off anything that did not fit, which is what clipped the
     recovery steps in the charger error card. Use HA's own
     --settings-row-body-padding-top/-bottom if the rows need to be tighter. */
  ha-settings-row {
    padding: 0;
    /* The row is two flex columns: the body with the heading, and a wrapper
       for the value slot. That wrapper is
         .prefix-wrap { flex: var(--settings-row-prefix-flex, 1); }
       so by default it grows and takes half the row -- 160px for a "87 %" --
       and the label wraps while there is room to spare. Both are the
       component's own knobs: a flex of 0 stops the wrapper growing, and a
       content width of auto lets the value take only what it needs. */
    --settings-row-prefix-flex: 0;
    --settings-row-content-width: auto;
  }

  ha-settings-row div.state {
    font-weight: 500;
    margin-right: 4px;
  }

  /* Every icon in a card or dialog. (This used to name the heading span as
     well, which a bare ha-icon already covers.) */
  ha-icon {
    color: var(--paper-item-icon-color, #44739e);
  }

  /* The bare ha-icon rule above paints every icon in this shadow root with the
     card palette. An icon inside a button is part of that button and has to
     follow its text colour -- otherwise the chevron on the back button stays
     blue while the label turns white. */
  ha-button ha-icon {
    color: inherit;
  }

  /* A secondary action should recede next to the primary one. The outlined
     button paints its border with
     var(--wa-color-border-loud, var(--wa-color-neutral-border-loud)), and
     'loud' is the darkest of the three rungs the component defines (loud,
     normal, quiet) -- which is why Back competed with Save.

     Set through the custom property on the host, not through ::part(button):
     a part only accepts styling when the component publishes it, and this one
     apparently does not (the rule built fine and changed nothing, twice).
     Custom properties inherit across the shadow boundary, so this reaches the
     component's own rule whatever it names its internals.

     The value is --wa-form-control-border-color: the same hairline the
     component library draws around inputs and selects, so the button's
     outline matches the fields it sits next to in the same dialog. Home
     Assistant maps it to --ha-color-border-neutral-quiet (#e6e6e6 in the
     default light theme), so light and dark mode both keep working -- which a
     fixed colour could not do.

     Note this goes against the component's intent: an outlined button has no
     fill, so its border is meant to carry the emphasis. If the hairline turns
     out to read as "not a button", move the secondary action to appearance
     'plain' rather than darkening this again -- that follows the design
     system instead of working around it. */
  ha-button[appearance~='outlined'] {
    --wa-color-border-loud: var(--wa-form-control-border-color);
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

  // The padding stuff does not work this way, the element is not reached with this css selectors.
  // ha-dialog::part(surface), ha-dialog div.mdc-dialog__content div.mdc-dialog__surface, .mdc-dialog__surface {
  //   padding: 8px !important;
  // }

  ha-dialog ha-markdown {
    margin-top: 16px;
  }


  `;
