# Power Load Balancer Configuration Guide

### **_This module is provided without any guarantees. Use it at your own risk!_**

The software has been tested with both hardware options and works fine. But authors do not take any responsibility for problems or harm caused by use of this software.

## Purpose

The primary reason for implementing this software is to address the failing firmware in the Wallbox Quasar 1 charger that in-activates the build-in loadbalancing when the charger is controlled via modbus.

By managing the power load dynamically, the system can prevent overloading. It only guards one phase as the Wallbox Quasar 1 is a one phase charger.

## Prerequisites

For the load balancer to work it needs information about the current load (how much power is consumed or produced) on the phase. This typically is done via one of these options:

- A cable that connects the Home Assistant hardware to the P1 port of your smart meter + the [DSMR Integration](https://www.home-assistant.io/integrations/dsmr/).

- A cable connection to connect a kWh meter (that was installed to work with the Quasar originally) to the Home Assistant hardware + a way to read the values from this device.

This should result in a (sensor) entity in Home Assistant that is updated frequently (at least every 5 to 10 seconds) with the actual load in Watt.

The DSMR integration does not provide this directly, it has separate entities for consumption and production power. These should be combined to one. Here's an example of how to do that in your config.yaml:

```yaml
sensor:
  - platform: template
    sensors:
      net_power_l1:
        friendly_name: "Net Power L1"
        unit_of_measurement: W
        device_class: power
        icon_template: mdi:counter
        value_template: "{{ ((float(states('sensor.electricity_meter_power_consumption_phase_l1')) - float(states('sensor.electricity_meter_power_production_phase_l1'))) * 1000)|int }}"
      net_power_l2:
        friendly_name: "Net Power L2"
        unit_of_measurement: W
        device_class: power
        icon_template: mdi:counter
        value_template: "{{ ((float(states('sensor.electricity_meter_power_consumption_phase_l2')) - float(states('sensor.electricity_meter_power_production_phase_l2'))) * 1000)|int }}"
      net_power_l3:
        friendly_name: "Net Power L3"
        unit_of_measurement: W
        device_class: power
        icon_template: mdi:counter
        value_template: "{{ ((float(states('sensor.electricity_meter_power_consumption_phase_l3')) - float(states('sensor.electricity_meter_power_production_phase_l3'))) * 1000)|int }}"
```

## Configuration

The load balancer is configured through the file `quasar_load_balancer.json` in the Home Assistant root folder (config). An example configuration file is provided there. Below is a detailed explanation of each configuration parameter and how to enable and configure the load balancer within a parent module.

1. **enabled**: `false`

   This parameter determines whether the power load balancer is active. Set it to `true` to enable the load balancer.

2. **quasar_host**: `x.x.x.x`

   The IP address of the Quasar host. Replace `x.x.x.x` with the actual IP address of your Quasar device.

3. **quasar_port**: `502`

   The port number on which the Quasar host communicates. The default is set to `502`.

4. **total_power_entity_id**: `sensor.your_power_entity_here`

   The entity ID used to track the instantaneous power reading in Watts. Replace `sensor.your_power_entity_here` with the actual sensor entity ID. See prerequisites. Make sure to choose the phase that the charger is connected to. If you aduse the yaml config above and your Quasar is connected to phase 2, the enity_id should be `sensor.net_power_l2`

5. **total_power_limit**: `pppp`

   The maximum power limit in Watts for a single phase of the homoe connection. E.g. a for a 3 x 25A connection use 25A x 230V = 5750W. Use an integer between 1380 and 7400.

6. **max_charge_power**: `mmmm`

   The maximum power in Watts that the Quasar can charge or discharge. This should be set according to the capabilities of your Quasar device, use an integer between 1380 and 7400.

7. **adjustment_delay**: `10`

   The delay in seconds before a new power limit is set. This helps in stabilizing the power adjustments.

8. **cooldown_period**: `15`
   - The period in seconds during which no new actions are taken after setting a new power limit. This prevents rapid fluctuations in power settings.

## Enabling the Load Balancer

To enable the power load balancer, set the `enabled` parameter to `true`. Additionally, adjust the following settings in the V2G Liberty settings page:

- **Charger host URL**: `127.0.0.1`
- **Port number**: `5020`

These settings ensure that the parent module communicates correctly with the load balancer.

## Note

Ensure that all IP addresses, entity IDs, and power limits are correctly configured to match your specific setup. Incorrect settings may lead to inefficient power management or system failures.

By following this guide, you can effectively configure and enable the power load balancer to optimize your system's power consumption and mitigate issues related to failing charger firmware.

Just to be sure you got this: This module is provided without any guarantees. Use it at your own risk.
