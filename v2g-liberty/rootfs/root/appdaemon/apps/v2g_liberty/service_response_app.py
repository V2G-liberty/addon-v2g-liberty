import random
import asyncio
import hassapi as hass

# This is a workaround for hass api lacking support for calling a.o. service calendar.get_events.
# With special thanks to chatziko, the creator of this code:
# https://gist.github.com/chatziko/74a5eacad3fd934d2ec734dab17aa4c0


class ServiceResponseApp(hass.Hass):
    def call_service(self, service, return_result=False, **kwargs):
        # standard call
        if not return_result:
            return super(ServiceResponseApp, self).call_service(service, **kwargs)

        # call with result, using the script wrapper
        res = asyncio.get_running_loop().create_future()
        call_id = random.randrange(2**32)

        def cb(name, data, kwargs):
            res.set_result(data["response"])

        self.listen_event(
            cb,
            "call_service_with_response.finished",
            call_id=call_id,
            oneshot=True,
        )

        self.call_service(
            "script/call_service_with_response",
            service_name=service,
            service_data=kwargs,
            call_id=call_id,
        )

        return res
