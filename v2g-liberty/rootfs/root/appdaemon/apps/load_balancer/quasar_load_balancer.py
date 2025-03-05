import asyncio
from appdaemon.plugins.hass.hassapi import Hass

from config_loader import ConfigLoader
from server import Tcp2TcpProxyServer
from request_modifier import RequestModifier
from load_balancer import LoadBalancer
from _log_wrapper import get_class_method_logger


class QuasarLoadBalancer(Hass):
    async def initialize(self):
        self.print = get_class_method_logger(self.__log)
        self.print("QuasarLoadBalancer: start initialise.")

        config = ConfigLoader().load()
        if not config.get("enabled"):
            self.print(
                "Not initialising quasar_load_balancer, not enabled in quasar_load_balancer.json."
            )
            # Eventhough the default is None, it can be intnitialised with a not None value and
            # cached, so, explicity set it to None. This indicates in the UI that the loadbalancer
            # is inactive.
            self.on_limit_changed(None)
            return

        total_power_entity_id = config.get("total_power_entity_id", None)
        if total_power_entity_id is None:
            self.print(
                "Not initialising quasar_load_balancer, total_power_entity_id not found in quasar_load_balancer.json."
            )
            return

        client_host = config.get("quasar_host", None)
        if client_host is None:
            self.print(
                "Not initialising quasar_load_balancer, client_host not found in quasar_load_balancer.json."
            )
            return

        client_port = config.get("quasar_port", 502)
        host = config.get("host", "127.0.0.1")
        port = config.get("port", 5020)

        # Default is None, setting this to 0 indicates in the UI that the loadbalancer is active.
        self.on_limit_changed(0)

        self.proxy_server = Tcp2TcpProxyServer(
            host=host, port=port, client_host=client_host, client_port=client_port
        )
        self.proxy_server.set_log(self.print)

        self.request_modifier = RequestModifier(proxy=self.proxy_server)
        self.request_modifier.on("limit", self.on_limit_changed)
        self.request_modifier.set_log(self.print)

        self.load_balancer = LoadBalancer(
            hass=self, rate_limiter=self.request_modifier, config=config
        )
        self.load_balancer.set_log(self.print)

        self.listen_state(self.on_total_power_changed, total_power_entity_id)
        self.timeout_timer = None
        self.reset_timeout()

        self.task = asyncio.create_task(self.proxy_server.run())

        self.print("QuasarLoadBalancer: complete initialise.")

    def on_limit_changed(self, new):
        self.set_state(
            "sensor.quasar_loadbalancer_limit",
            state=new,
            attributes={"unit_of_measurement": "W"},
        )

    def on_total_power_changed(self, entity, attribute, old, new, kwargs):
        total_power = int(new)
        self.load_balancer.total_power_changed(total_power)
        self.reset_timeout()

    def reset_timeout(self):
        self.set_state("input_boolean.quasar_loadbalancer_no_total_power", state="off")
        if self.timeout_timer is not None:
            self.cancel_timer(self.timeout_timer)
        self.timeout_timer = self.run_in(self.total_power_changed_timeout, 60)

    def total_power_changed_timeout(self, kwargs):
        self.reset_timeout()
        self.set_state("input_boolean.quasar_loadbalancer_no_total_power", state="on")
        self.print("No new total power received for more than 60 sec.")

    def __log(self, msg):
        self.log(msg=msg, level="INFO")
