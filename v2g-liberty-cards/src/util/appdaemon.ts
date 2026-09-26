import { HomeAssistant } from 'custom-card-helpers';
import { HassEvent } from 'home-assistant-js-websocket';

/**
 * How long a settings card waits for the add-on before it gives up. Short on
 * purpose: callFunction's own default is a minute, and a card that cannot
 * reach the add-on should say so rather than leave the user at a spinner. The
 * cards offer a Retry and reload themselves once the add-on reports it has
 * started.
 */
export const SETTINGS_LOAD_TIMEOUT_MS = 10000;

export function callFunction(
  hass: HomeAssistant,
  eventName: string,
  args: { [key: string]: any } = {},
  timeoutInMillsec: number = 60 * 1000
): Promise<{ [key: string]: any }> {
  return new Promise(async (resolve, reject) => {
    const unsubscribe = await hass.connection.subscribeEvents<HassEvent>(
      onResult,
      `${eventName}.result`
    );
    hass.callApi('POST', `events/${eventName}`, args);
    const timeoutId = setTimeout(onTimeout, timeoutInMillsec);

    function onResult(event: HassEvent): void {
      clearTimeout(timeoutId);
      unsubscribe();
      resolve(event.data);
    }

    function onTimeout(): void {
      console.error(`"${eventName}" timed out after ${timeoutInMillsec}msec.`);
      unsubscribe();
      reject(
        new Error(`"${eventName}" timed out after ${timeoutInMillsec}msec.`)
      );
    }
  });
}
