import * as Polyglot from 'node-polyglot';

import * as en from '../strings.json';
import * as nl from '../translations/nl.json';

const polyglot = initialize();

function initialize(): Polyglot {
  const languages = { en, nl };
  const lang = navigator.language.split('-')[0];
  let polyglot = new Polyglot({
    phrases: en,
    allowMissing: true,
    onMissingKey: key => {
      // console.error(`Cannot translate '${key}'`);
      return null;
    },
  });
  if (lang !== 'en' && languages[lang]) {
    const fallback = polyglot;
    polyglot = new Polyglot({
      phrases: languages[lang],
      allowMissing: true,
      onMissingKey: (key, options) => fallback.t(key, options),
    });
  }
  return polyglot;
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
