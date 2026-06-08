import * as Polyglot from 'node-polyglot';

import * as en from '../strings.json';
import * as nl from '../translations/nl.json';

const LANGUAGES: Record<string, any> = { en, nl };

// English fallback polyglot — used as the safety net for missing keys
// in any other language.
const fallback = new Polyglot({
  phrases: en,
  allowMissing: true,
  onMissingKey: () => null,
});

let currentLang = 'en';
let polyglot: Polyglot = fallback;

function buildPolyglot(lang: string): Polyglot {
  if (lang === 'en' || !LANGUAGES[lang]) return fallback;
  return new Polyglot({
    phrases: LANGUAGES[lang],
    allowMissing: true,
    onMissingKey: (key, options) => fallback.t(key, options),
  });
}

/**
 * Set the active UI language. Cards should call this from their `set hass()`
 * with `hass.locale?.language ?? hass.language ?? 'en'` so the cards follow
 * the user's Home Assistant language preference instead of the browser locale.
 */
export function setLanguage(lang: string | null | undefined): void {
  const normalised = (lang ?? 'en').split('-')[0];
  if (normalised === currentLang) return;
  currentLang = normalised;
  polyglot = buildPolyglot(normalised);
}

export function t(
  phrase: string,
  options?: Polyglot.InterpolationOptions
): string {
  return polyglot.t(phrase, options);
}

export function partial(
  prefix: string
): (key: string, options?: Polyglot.InterpolationOptions) => string {
  return (key, options) => polyglot.t(`${prefix}.${key}`, options);
}

export const to = partial('option');
