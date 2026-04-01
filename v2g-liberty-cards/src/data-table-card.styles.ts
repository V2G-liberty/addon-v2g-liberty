import { css } from 'lit';

export const dataTableStyles = css`
    :host {
      display: block;
      max-height: calc(100vh - var(--header-height, 56px));
      overflow-y: auto;
      padding: 12px;
      box-sizing: border-box;
      container-type: inline-size;
      --v2g-profit-colour: #66A802;

      /* Data-inzicht colour palette */
      --di-green-100: #70B603;
      --di-green-92: #66A802;
      --di-green-84: #5E9903;
      --di-green-tint-80: #8DC556;
      --di-green-68: #4C7B02;
      --di-slate-100: #002E4E;
      --di-slate-80: #345470;
      --di-slate-60: #637C92;
      --di-slate-40: #95A6B5;
      --di-slate-20: #C9D2DA;
      --di-teal-100: #60B9B6;
      --di-teal-tint-20: #E8F5F4;
      --di-teal-dark-1: #346A68;
      --di-teal-dark-2: #0E2424;
      --di-bg: #FFF8F5;
      --di-netto-bg: var(--di-teal-tint-20);
      --di-netto-border: var(--di-teal-100);
    }

    /* ─- Page header ──────────────────────────────── */

    .page-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin: 12px 0 24px 0;
    }

    .page-title {
      margin: 0 0 0 24px;
      font-size: var(--ha-card-header-font-size, 1.5rem);
      font-weight: 500;
      line-height: 1.2;
      color: var(--ha-card-header-color, var(--primary-text-color));
    }

    .page-title .date-range {
      color: var(--di-teal-100);
    }

    /* ─- Overflow menu ─────────────────────────────── */

    .overflow-menu {
      position: relative;
      margin-right: 8px;
    }

    .overflow-dropdown {
      position: absolute;
      right: 0;
      top: 100%;
      z-index: 100;
      min-width: 180px;
      background: var(--card-background-color, #fff);
      border-radius: 8px;
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
      list-style: none;
      margin: 0;
      padding: 4px 0;
    }

    .overflow-item {
      display: block;
      width: 100%;
      padding: 8px 16px;
      border: none;
      background: none;
      text-align: left;
      cursor: pointer;
      font-size: 14px;
      font-family: inherit;
      color: var(--primary-text-color);
    }
    .overflow-item:hover {
      background: var(--secondary-background-color);
    }

    /* ─- Page layout ──────────────────────────────── */

    .page-layout {
      display: flex;
      flex-direction: column;
      gap: 12px;
    }

    /* ── Sub-cards grid ──────────────────────────── */

    .totals-subcards-grid {
      display: grid;
      grid-template-columns: repeat(10, 1fr);
      gap: 12px;
    }

    .subcard-netto     { grid-column: 1 / 8; }
    .subcard-savings   { grid-column: 8 / 11; }
    .subcard-charge    { grid-column: 1 / 6; }
    .subcard-discharge { grid-column: 6 / 11; }

    @container (max-width: 700px) {
      .totals-subcards-grid {
        grid-template-columns: 1fr;
      }
      .subcard-netto, .subcard-savings,
      .subcard-charge, .subcard-discharge {
        grid-column: 1 / -1;
      }
    }

    /* ── Base sub-card ───────────────────────────── */

    .subcard {
      border-radius: 12px;
      border: 1px solid var(--divider-color, #e0e0e0);
      padding: 20px 24px;
      background: var(--card-background-color, #fff);
      position: relative;
    }

    /* ── NETTO card — teal tinted background ─────── */

    .subcard-netto {
      background: var(--di-netto-bg);
      border-color: var(--di-netto-border);
    }

    /* ── Sub-card header ─────────────────────────── */

    .subcard-header {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 16px;
    }

    .subcard-header ha-icon {
      --mdc-icon-size: 24px;
      color: var(--di-slate-60);
    }

    .subcard-title {
      font-size: 14px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--di-slate-60);
    }

    /* ── Hero value (Netto card — days+ only) ────── */

    .subcard-hero {
      font-size: 32px;
      font-weight: 600;
      color: var(--primary-text-color);
      margin-bottom: 16px;
    }

    .subcard-hero .hero-unit {
      font-size: 18px;
      font-weight: 400;
      color: var(--secondary-text-color);
    }

    /* ── Metric grid (4 columns, single row) ─────── */

    .metric-grid {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 4px 16px;
    }

    .metric {
      display: flex;
      flex-direction: column;
      gap: 2px;
      min-width: 0;
    }

    .metric-label {
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--di-slate-40);
      white-space: nowrap;
    }

    .metric-value {
      font-size: 18px;
      font-weight: 500;
      color: var(--primary-text-color);
      white-space: nowrap;
    }

    .metric-value .metric-unit {
      font-size: 13px;
      font-weight: 400;
      color: var(--secondary-text-color);
    }

    .metric-grid.cols-3 {
      grid-template-columns: repeat(3, 1fr);
    }

    /* ── Savings card — green gradient ───────────── */

    .subcard-savings {
      background: linear-gradient(160deg, var(--di-green-100) 0%, var(--di-green-68) 100%);
      border-color: var(--di-green-92);
      color: #fff;
      display: flex;
      flex-direction: column;
      position: relative;
    }

    .subcard-savings .savings-piggy {
      position: absolute;
      top: 16px;
      right: 16px;
      --mdc-icon-size: 32px;
      color: var(--di-teal-tint-20);
    }

    .subcard-savings .savings-title-row {
      display: flex;
      align-items: center;
      gap: 4px;
      /* Keep clear of the piggy icon */
      margin-right: 40px;
      min-width: 0;
    }

    .subcard-savings .subcard-title {
      font-size: 32px;
      font-weight: 600;
      color: var(--di-slate-100);
      text-transform: none;
      line-height: 0.8;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      min-width: 0;
    }

    .subcard-savings .savings-title-row .info-icon {
      flex-shrink: 0;
      color: var(--di-teal-tint-20);
    }

    .subcard-savings .savings-sublabel {
      font-size: 13px;
      color: var(--di-bg);
      margin-top: 2px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      margin-right: 40px;
    }

    .subcard-savings .subcard-hero {
      color: var(--di-bg);
      font-size: 48px;
      font-weight: normal;
      text-align: center;
      flex: 1;
      display: flex;
      align-items: center;
      justify-content: center;
    }

    .subcard-savings .savings-dyn {
      display: flex;
      align-items: baseline;
      gap: 6px;
    }

    .subcard-savings .savings-dyn-amount {
      font-size: 18px;
      font-weight: 500;
      color: rgba(255, 255, 255, 0.85);
    }

    .subcard-savings .savings-dyn-label {
      font-size: 13px;
      color: rgba(255, 255, 255, 0.7);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      min-width: 0;
    }

    /* ── Floating bar ─────────────────────────────── */

    .floating-bar {
      position: fixed;
      bottom: 12px;
      left: var(--mdc-drawer-width, 0px);
      right: 0;
      display: flex;
      justify-content: center;
      z-index: 5;
    }

    .bar-content {
      display: flex;
      align-items: center;
      gap: 4px;
      background: var(--card-background-color, white);
      border-radius: 12px;
      padding: 4px 8px;
      box-shadow: 0px 4px 12px rgba(0, 0, 0, 0.25);
      min-height: 48px;
    }

    .bar-separator {
      width: 1px;
      height: 24px;
      background: var(--divider-color, #e0e0e0);
      margin: 0 4px;
    }

    .pill {
      border: none;
      border-radius: 18px;
      padding: 6px 12px;
      font-size: 13px;
      background: transparent;
      color: var(--primary-text-color);
      cursor: pointer;
      white-space: nowrap;
    }

    .pill:hover {
      background: var(--secondary-background-color, #f5f5f5);
    }

    .pill.active {
      background: var(--primary-color);
      color: var(--text-primary-color, #fff);
    }

    /* ── Granularity dropdown (narrow bar) ──────── */

    .gran-menu {
      position: relative;
    }

    .gran-trigger {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      background: var(--primary-color);
      border: none;
      border-radius: 20px;
      padding: 6px 8px 6px 14px;
      font-size: 13px;
      font-family: inherit;
      color: var(--text-primary-color, #fff);
      cursor: pointer;
      white-space: nowrap;
    }

    .gran-trigger:hover {
      opacity: 0.85;
    }

    .gran-dropdown {
      position: absolute;
      bottom: calc(100% + 6px);
      right: 0;
      background: var(--card-background-color, #fff);
      border-radius: 12px;
      box-shadow: 0 4px 16px rgba(0, 0, 0, 0.2);
      min-width: 140px;
      z-index: 100;
      list-style: none;
      margin: 0;
      padding: 4px 0;
    }

    .gran-item {
      display: block;
      width: 100%;
      text-align: left;
      background: none;
      border: none;
      padding: 10px 16px;
      font-size: 14px;
      font-family: inherit;
      color: var(--primary-text-color);
      cursor: pointer;
    }

    .gran-item:hover {
      background: var(--secondary-background-color, rgba(0, 0, 0, 0.04));
    }

    .gran-item.active {
      color: var(--primary-color, #1976d2);
      font-weight: 500;
    }

    .gran-dropdown li:first-child .gran-item {
      border-radius: 12px 12px 0 0;
    }

    .gran-dropdown li:last-child .gran-item {
      border-radius: 0 0 12px 12px;
    }

    .date-wrapper {
      position: relative;
      text-align: center;
      cursor: pointer;
      min-width: 130px;
    }

    .date-label {
      font-size: 14px;
      font-weight: 500;
      color: var(--primary-text-color);
    }

    .date-input {
      position: absolute;
      top: 0;
      left: 0;
      width: 100%;
      height: 100%;
      opacity: 0;
      cursor: pointer;
    }

    /* ── Table ─────────────────────────────────────── */

    .table-container {
      /* No own scroll — :host is the scroll container */
    }

    table {
      width: 100%;
      border-collapse: separate;
      border-spacing: 0;
      font-size: 13px;
      font-variant-numeric: tabular-nums;
      /* Whitespace below last row; works because border-collapse is separate */
      padding-bottom: 48px;
    }

    thead {
      position: sticky;
      top: 0;
      z-index: 3;
      box-shadow: 0 1px 0 var(--divider-color, #e0e0e0);
      transition: box-shadow 0.2s ease;
    }

    .table-container.scrolled thead {
      box-shadow: 0 1px 0 var(--divider-color, #e0e0e0),
                  0 2px 4px rgba(0, 0, 0, 0.1);
    }

    thead th {
      text-align: left;
      vertical-align: top;
      padding: 8px 12px;
      font-weight: 500;
      font-size: 12px;
      color: var(--primary-text-color);
      background: var(--card-background-color, white);
    }

    thead th.group-sep {
      position: relative;
    }

    /* Single-row thead: gap at top and bottom */
    thead:not(.grouped) th.group-sep::before {
      content: '';
      position: absolute;
      left: 0;
      width: 1px;
      top: 20%;
      bottom: 20%;
      background: var(--divider-color, #e0e0e0);
    }

    /* Grouped thead first row: gap only at top, line continues into sub-header */
    thead.grouped tr:first-child th.group-sep::before {
      content: '';
      position: absolute;
      left: 0;
      width: 1px;
      top: 30%;
      bottom: 0;
      background: var(--divider-color, #e0e0e0);
    }

    /* Grouped thead sub-header row: gap only at bottom, continues from first row */
    thead.grouped tr.sub-header th.group-sep::before {
      content: '';
      position: absolute;
      left: 0;
      width: 1px;
      top: 0;
      bottom: 30%;
      background: var(--divider-color, #e0e0e0);
    }

    thead th .unit {
      display: block;
      font-weight: 400;
      font-size: 11px;
      color: var(--secondary-text-color, #797979);
    }

    thead th.num {
      text-align: right;
    }

    .wide-title-wrapper {
      display: block;
      width: 55px;
      min-width: 100%;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    /* ── Grouped two-row header (hours/days view) ─────── */

    .group-header {
      text-align: left;
      font-weight: 600;
    }

    thead.grouped tr.sub-header th {
      font-size: 11px;
      font-weight: 400;
      color: var(--secondary-text-color, #797979);
      padding-top: 4px;
      padding-bottom: 4px;
    }

    tbody td.group-sep::before {
      content: '';
      position: absolute;
      left: 0;
      width: 1px;
      top: 20%;
      bottom: 20%;
      background: var(--divider-color, #e0e0e0);
    }

    tbody td.group-sep {
      position: relative;
    }

    tbody td.group-sep::before {
      top: 20%;
      bottom: 20%;
    }

    tbody td {
      padding: 10px 12px;
      border-bottom: 1px solid var(--divider-color, #e0e0e0);
      white-space: nowrap;
    }

    /* Border around the table header: top, left, right */
    thead tr:first-child th {
      border-top: 1px solid var(--divider-color, #e0e0e0);
    }
    thead tr:first-child th:first-child {
      border-top-left-radius: 8px;
    }
    thead tr:first-child th:last-child {
      border-top-right-radius: 8px;
    }
    thead th:first-child {
      border-left: 1px solid var(--divider-color, #e0e0e0);
    }
    thead th:last-child {
      border-right: 1px solid var(--divider-color, #e0e0e0);
    }

    thead th:first-child,
    tbody td:first-child {
      padding-left: 24px;
    }

    thead th:last-child,
    tbody td:last-child {
      padding-right: 24px;
    }

    tbody td.num {
      text-align: right;
    }

    .profit {
      color: var(--v2g-profit-colour);
    }

    tbody tr:hover {
      background: var(--table-row-alternative-background-color, #f9f9f9);
    }

    .center {
      display: flex;
      justify-content: center;
      align-items: center;
      gap: 8px;
      padding: 24px 0;
    }

    @keyframes spin {
      to { transform: rotate(360deg); }
    }

    .spinner {
      display: inline-block;
      width: 18px;
      height: 18px;
      border: 2px solid var(--secondary-text-color);
      border-top-color: var(--primary-color);
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
      flex-shrink: 0;
    }

    .muted {
      color: var(--secondary-text-color);
      font-size: 14px;
    }

    .no-data-msg {
      display: flex;
      flex-direction: column;
      align-items: center;
      text-align: center;
    }

    tr.repaired {
      color: var(--secondary-text-color);
    }

    .estimated-note {
      position: absolute;
      top: 16px;
      right: 16px;
      color: var(--secondary-text-color);
      font-size: 12px;
      margin: 0;
      font-style: italic;
      text-align: right;
    }

    .error {
      color: var(--error-color, #db4437);
      font-size: 14px;
    }

    /* ── Price indicator ───────────────────────────── */

    .indicator-col,
    .indicator-cell {
      text-align: center;
    }

    .state-cell {
      position: relative;
      display: inline-block;
    }

    .state-plus {
      position: absolute;
      top: -2px;
      right: -6px;
      font-size: 10px;
      font-weight: 700;
      color: var(--primary-color);
      line-height: 1;
    }

    /* ── Price sparkline track ─────────────────────── */

    .price-track {
      position: relative;
      display: inline-flex;
      align-items: center;
      width: 48px;
      height: 20px;
    }

    .price-track::before {
      content: '';
      position: absolute;
      left: 0;
      width: calc(var(--marker-left) - 7px);
      top: 50%;
      height: 1px;
      background: var(--secondary-text-color);
    }

    .price-track::after {
      content: '';
      position: absolute;
      left: calc(var(--marker-left) + 7px);
      right: 0;
      top: 50%;
      height: 1px;
      background: var(--secondary-text-color);
    }

    .price-marker {
      position: absolute;
      font-size: 12px;
      font-weight: 700;
      line-height: 1;
      transform: translateX(-50%);
      color: var(--marker-color);
      left: var(--marker-left);
      z-index: 1;
      user-select: none;
    }

    /* Light mode */
    .price-track[data-level='very-low']  { --marker-left: 8%;  --marker-color: #90caf9; }
    .price-track[data-level='low']       { --marker-left: 28%; --marker-color: #5c8dc9; }
    .price-track[data-level='average']   { --marker-left: 50%; --marker-color: #7e57c2; }
    .price-track[data-level='high']      { --marker-left: 72%; --marker-color: #6a1b9a; }
    .price-track[data-level='very-high'] { --marker-left: 92%; --marker-color: #4a0072; }

    /* Dark mode: detected via hass.themes.darkMode → .dark class on host */
    :host(.dark) {
      --v2g-profit-colour: #8DC556;
      --di-netto-bg: var(--di-teal-dark-2);
      --di-netto-border: var(--di-teal-dark-1);
    }

    :host(.dark) .price-track[data-level='very-low']  { --marker-color: #37474f; }
    :host(.dark) .price-track[data-level='low']       { --marker-color: #5c6bc0; }
    :host(.dark) .price-track[data-level='average']   { --marker-color: #9575cd; }
    :host(.dark) .price-track[data-level='high']      { --marker-color: #ba68c8; }
    :host(.dark) .price-track[data-level='very-high'] { --marker-color: #e040fb; }

    /* ── Totals card ───────────────────────────────── */

    .info-container {
      position: relative;
      display: inline-block;
      vertical-align: middle;
      margin-left: 2px;
    }

    .info-icon {
      width: 14px;
      height: 14px;
      color: var(--primary-color);
      cursor: pointer;
      display: block;
    }

    .info-popup {
      position: absolute;
      top: calc(100% + 4px);
      left: 50%;
      transform: translateX(-50%);
      text-align: left;
      text-transform: none;
      letter-spacing: normal;
      background: var(--primary-text-color);
      color: var(--card-background-color);
      padding: 6px 10px;
      border-radius: 4px;
      font-size: 12px;
      font-weight: 400;
      line-height: 1.4;
      width: 280px;
      white-space: pre-line;
      z-index: 100;
      cursor: default;
    }

    .info-popup::before {
      content: '';
      position: absolute;
      bottom: 100%;
      left: 50%;
      transform: translateX(-50%);
      border: 6px solid transparent;
      border-bottom-color: var(--primary-text-color);
      pointer-events: none;
    }

    .estimated-note .info-popup {
      left: auto;
      right: 0;
      transform: none;
    }

    .estimated-note .info-popup::before {
      left: auto;
      right: 10px;
      transform: none;
    }

`;
