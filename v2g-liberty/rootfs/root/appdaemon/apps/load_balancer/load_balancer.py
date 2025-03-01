import statistics

from _log_wrapper import get_class_method_logger


class LoadBalancer:
    def __init__(self, hass, rate_limiter, config):
        power_limit = int(config.get("total_power_limit", 5750))
        max_charge_power = int(config.get("max_charge_power", 3680))
        adjustment_delay = int(config.get("adjustment_delay", 10))
        cooldown_period = int(config.get("cooldown_period", 15))

        self.total_power_upper_limit = power_limit
        self.total_power_lower_limit = power_limit - 230
        self.max_charge_power = max_charge_power
        self.min_charge_power = 1 # Watt

        self.hass = hass

        self.rate_limiter = rate_limiter
        self.rate_limiter.set_limit(self.max_charge_power)

        self.adjustment_delay = adjustment_delay
        self.high_timer = None
        self.low_timer = None
        self.high_values = []
        self.low_values = []
        self.cooldown_period = cooldown_period
        self.cooldown_timer = None
        self.log = get_class_method_logger(print)

    def set_log(self, log):
        self.log = log

    def total_power_changed(self, total_power):
        if self.cooldown_timer:
            # self.log("Cooldown active. Skipping adjustment.")
            # log.log("Cooldown active. Skipping adjustment.")
            return

        total_power = abs(total_power)
        current_limit = self.rate_limiter.limit

        if total_power > self.total_power_upper_limit:
            self.log(
                f"Total power {total_power}W exceeds {self.total_power_upper_limit}W."
            )
            self.reset_low_timer()
            self.set_high_timer()
            self.high_values.append(total_power - self.total_power_upper_limit)

        elif (
            total_power < self.total_power_lower_limit
            and current_limit < self.max_charge_power
        ):
            self.log(
                f"Total power {total_power}W below {self.total_power_lower_limit}W."
            )
            self.reset_high_timer()
            self.set_low_timer()
            self.low_values.append(self.total_power_lower_limit - total_power)

        else:
            if self.high_timer or self.low_timer:
                self.log(
                    f"Total power {total_power}W within acceptable range. Resetting timers and data."
                )
            self.reset_high_timer()
            self.reset_low_timer()

    def set_high_timer(self):
        if not self.high_timer:
            self.high_timer = self.hass.run_in(self.reduce_power, self.adjustment_delay)

    def reset_high_timer(self):
        if self.high_timer:
            self.hass.cancel_timer(self.high_timer)
            self.high_timer = None
            self.high_values = []

    def set_low_timer(self):
        if not self.low_timer:
            self.low_timer = self.hass.run_in(
                self.increase_power, self.adjustment_delay
            )

    def reset_low_timer(self):
        if self.low_timer:
            self.hass.cancel_timer(self.low_timer)
            self.low_timer = None
            self.low_values = []

    def reduce_power(self, kwargs):
        median_adjustment = int(statistics.median(self.high_values))
        current_limit = self.rate_limiter.limit
        new_limit = max(self.min_charge_power, current_limit - median_adjustment)
        self.rate_limiter.set_limit(new_limit)
        self.log(f"Reducing power by {median_adjustment}W to {new_limit}W.")

        self.start_cooldown()
        self.high_timer = None
        self.high_values = []

    def increase_power(self, kwargs):
        median_adjustment = int(statistics.median(self.low_values))
        current_limit = self.rate_limiter.limit
        new_limit = min(self.max_charge_power, current_limit + median_adjustment)
        self.rate_limiter.set_limit(new_limit)
        self.log(f"Increasing power by {median_adjustment}W to {new_limit}W.")

        self.start_cooldown()
        self.low_timer = None
        self.low_values = []

    def start_cooldown(self):
        """Start de cooldownperiode na een aanpassing."""
        self.log("Starting cooldown period.")
        self.cooldown_timer = self.hass.run_in(self.end_cooldown, self.cooldown_period)

    def end_cooldown(self, kwargs):
        """Beëindig de cooldownperiode."""
        self.log("Cooldown period ended.")
        self.cooldown_timer = None
