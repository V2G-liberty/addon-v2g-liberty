import { HomeAssistant } from 'custom-card-helpers';
import { HassEvent } from 'home-assistant-js-websocket';

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
