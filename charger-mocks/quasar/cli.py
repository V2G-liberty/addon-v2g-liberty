#!/usr/bin/env python3
"""
Wallbox Quasar Mock CLI

A command-line interface for controlling the Wallbox Quasar Mock Modbus server.
Developed for V2G Liberty testing and development.

Usage:
    python cli.py
    python cli.py --host localhost --port 5020
    python cli.py -H 192.168.1.100 -p 5020 -v

For more information: https://github.com/V2G-liberty/addon-v2g-liberty
"""

import cmd
import argparse
import sys
from pyModbusTCP.client import ModbusClient
import time
import random

# Import Quasar-specific definitions
from register_map import (
    CONTROL_MODE_REGISTER,
    CHARGER_STATE_REGISTER,
    SOC_REGISTER,
    INTERNAL_ERROR_REGISTER,
    ACTUAL_POWER_REGISTER,
    REQUESTED_POWER_REGISTER,
    MAX_AVAILABLE_POWER_REGISTER,
    MAX_UNSIGNED_SHORT,
    MIN_POWER_WATTS,
    MAX_POWER_WATTS,
    MIN_MAX_POWER,
    MAX_MAX_POWER,
    MIN_SOC_PERCENT,
    MAX_SOC_PERCENT,
    encode_signed_power,
    decode_signed_power
)
from states import (
    CHARGER_STATES,
    STATE_DISCONNECTED,
    STATE_CHARGING,
    STATE_APP_CONTROLLED,
    STATE_PAUSED,
    STATE_ERROR,
    STATE_DISCHARGING
)

__version__ = "2.1.0"


class QuasarMockCLI(cmd.Cmd):
    """
    A minimal CLI for controlling the Wallbox Quasar Mock.

    Facilitates testing of V2G Liberty (Modbus) code by enabling test scenarios:

    Fatal error scenarios:
    1. Set error: Sets charger_state to error (7)
    2. Set internal_error: Sets error entity value to non-zero
    3. Stop the mock docker
    4. Do 1-3 with car disconnected => should result in non-critical notification
    5. Set soc to 0 (considered an invalid value)

    Note: Uses synchronous Modbus client for simplicity.
    Future improvement: Consider Textual framework (https://textual.textualize.io)
    """

    client: ModbusClient
    prompt = 'Quasar: '
    intro = 'Wallbox Quasar Mock CLI: Send Modbus commands to the mock charger. Type "help" for available commands.'

    def __init__(self, host='localhost', port=5020, verbose=False):
        """Initialize CLI with configurable host and port"""
        super().__init__()
        self.host = host
        self.port = port
        self.verbose = verbose
        self.client = None

    def preloop(self):
        """Setup Modbus client before entering command loop"""
        print(f"\n{'='*60}")
        print(f"Wallbox Quasar Mock CLI v{__version__}")
        print(f"Connecting to Modbus server at {self.host}:{self.port}")
        print(f"{'='*60}\n")

        self.client = ModbusClient(
            host=self.host,
            port=self.port,
            auto_open=True,
            auto_close=True,
            timeout=5.0
        )

        # Test connection
        if not self._test_connection():
            print(f"\n⚠️  ERROR: Could not connect to Modbus server at {self.host}:{self.port}")
            print("   Please ensure:")
            print("   1. Docker mock server is running")
            print("   2. Host/port are correct")
            print("   3. No firewall is blocking the connection\n")
            sys.exit(1)

        if self.verbose:
            print(f"[VERBOSE] Client object: {self.client}")

        print(f"✓ Connected successfully to {self.host}:{self.port}\n")

        # Auto-start follow mode
        self.do_follow(1)

    def _test_connection(self):
        """Test if we can read from the mock server"""
        try:
            # Try to read charger state register
            result = self.client.read_holding_registers(CHARGER_STATE_REGISTER, 1)
            return result is not None
        except Exception as e:
            if self.verbose:
                print(f"[VERBOSE] Connection test failed: {e}")
            return False

    def read_settings_loop(self):
        """Continuous status monitoring loop"""
        while True:
            self._show_status()
            time.sleep(10)

    def _set_state(self, state: int):
        """Set charger state via Modbus"""
        self.client.write_single_register(CHARGER_STATE_REGISTER, state)
        time.sleep(1)

    def do_app_control(self, line):
        """App_control: Sets charger in state where a scheduled charge has been set through the
        app and V2G Liberty cannot take control"""
        self._set_state(STATE_APP_CONTROLLED)
        time.sleep(1)
        self._show_status()

    def do_connect(self, line):
        """Connect: Set charger state to connected and paused"""
        self._set_state(STATE_PAUSED)
        self._show_status()

    def do_error(self, line):
        """Error: Set charger state to error"""
        self._set_state(STATE_ERROR)
        self._set_actual_power(0)
        self._show_status()

    def do_no_error(self, line):
        """No_error: Clear error, set charger state to connected/paused"""
        self._set_state(STATE_PAUSED)
        self._show_status()

    def do_ie(self, line):
        """ie (Internal Error): Set unrecoverable_errors_register_high"""
        self._set_actual_power(0)
        self.client.write_single_register(INTERNAL_ERROR_REGISTER, 1234)
        self._show_status()

    def do_no_ie(self, line):
        """no_ie: Clear internal error (unrecoverable_errors_register_high)"""
        self.client.write_single_register(INTERNAL_ERROR_REGISTER, 0)
        self._show_status()

    def do_disconnect(self, line):
        """Disconnect: Set charger state to disconnected, soc and power to 0"""
        self._set_state(STATE_DISCONNECTED)
        self._set_actual_power(0)
        self._set_soc(0)
        self._show_status()

    def do_set_max_power(self, power):
        """Set_max_power <watts>: Set maximum available power (1-7400 W)"""
        try:
            power = int(float(power))
            if power < MIN_MAX_POWER:
                print(f"⚠️  Power must be at least {MIN_MAX_POWER} W")
                return
            if power > MAX_MAX_POWER:
                print(f"⚠️  Power cannot exceed {MAX_MAX_POWER} W")
                return
            self.client.write_single_register(MAX_AVAILABLE_POWER_REGISTER, power)
            time.sleep(1)
            self._show_status()
        except ValueError:
            print("⚠️  Invalid power value. Please provide a number.")

    def _set_actual_power(self, power: int):
        """Set actual power output (internal method)"""
        encoded_power = encode_signed_power(power)
        self.client.write_single_register(ACTUAL_POWER_REGISTER, encoded_power)
        time.sleep(1)

    def _set_requested_power(self, power: int):
        """Set requested power with automatic clamping to max available power"""
        max_power = self.client.read_holding_registers(MAX_AVAILABLE_POWER_REGISTER)
        max_power = int(float(max_power[0]))

        # Clamp to max available power
        if power > max_power:
            power = max_power
        elif -power > max_power:
            power = -max_power

        encoded_power = encode_signed_power(power)
        self.client.write_single_register(REQUESTED_POWER_REGISTER, encoded_power)
        time.sleep(1)
        self.do_follow(1)

    def do_follow(self, line):
        """Follow the current setting for the requested power"""
        req_power = self.client.read_holding_registers(REQUESTED_POWER_REGISTER)
        req_power = decode_signed_power(int(float(req_power[0])))

        # Set appropriate state based on requested power
        if req_power == 0:
            self._set_state(STATE_PAUSED)
        elif req_power > 0:
            self._set_state(STATE_CHARGING)
        elif req_power < 0:
            self._set_state(STATE_DISCHARGING)

        # Simulate actual power as 81-97% of requested power
        power = int(random.uniform(0.81, 0.97) * req_power)

        self._set_actual_power(power=power)
        time.sleep(1)
        self._show_status()

    def do_charge(self, power):
        """Charge <watts>: Set charging power (-7400 to 7400 W). Negative = discharging (V2G)"""
        try:
            power = int(float(power))
            if power < MIN_POWER_WATTS or power > MAX_POWER_WATTS:
                print(f"⚠️  Power must be between {MIN_POWER_WATTS} and {MAX_POWER_WATTS} W")
                return
            self._set_requested_power(power)
            self._show_status()
        except ValueError:
            print("⚠️  Invalid power value. Please provide a number.")

    def do_take_control(self, line):
        """Take_control: Set control register to Remote (usually automatic)"""
        self.client.write_single_register(CONTROL_MODE_REGISTER, 0)
        time.sleep(1)
        self._show_status()

    def _set_soc(self, soc: int):
        """Set state of charge (internal method)"""
        self.client.write_single_register(SOC_REGISTER, soc)
        time.sleep(1)

    def do_soc(self, soc):
        """Soc <percent>: Set state of charge (0-100%)"""
        try:
            soc = int(float(soc))
            if soc < MIN_SOC_PERCENT or soc > MAX_SOC_PERCENT:
                print(f"⚠️  SoC must be between {MIN_SOC_PERCENT} and {MAX_SOC_PERCENT}%")
                return
            self._set_soc(soc)
            self._show_status()
        except ValueError:
            print("⚠️  Invalid SoC value. Please provide a number.")

    def do_status(self, line):
        """Status: Display current charger status"""
        self._show_status()

    def _show_status(self):
        """Display current charger status"""
        # Control mode
        control = self.client.read_holding_registers(CONTROL_MODE_REGISTER)
        print("Control (0=user, 1=remote): " + str(control[0]))

        # Internal error status
        internal_error = self.client.read_holding_registers(INTERNAL_ERROR_REGISTER)
        internal_error = internal_error[0]
        error_msg = " (No internal error)" if internal_error == 0 else f" (Internal error: {internal_error})"

        # Charger state
        status = self.client.read_holding_registers(CHARGER_STATE_REGISTER)
        status = int(float(status[0]))
        print("state: " + CHARGER_STATES[status] + error_msg)

        # Power status
        power = self.client.read_holding_registers(ACTUAL_POWER_REGISTER)
        power = decode_signed_power(int(float(power[0])))

        req_power = self.client.read_holding_registers(REQUESTED_POWER_REGISTER)
        req_power = decode_signed_power(int(float(req_power[0])))

        max_power = self.client.read_holding_registers(MAX_AVAILABLE_POWER_REGISTER)
        max_power = int(float(max_power[0]))
        print(f"power: {power} W  (req.: {req_power} W, max.: {max_power} W)")

        # State of charge
        soc = self.client.read_holding_registers(SOC_REGISTER)
        print(f"soc  : {soc[0]} %")

    def do_version(self, line):
        """Version: Show CLI version"""
        print(f"Wallbox Quasar Mock CLI v{__version__}")

    def do_connection(self, line):
        """Connection: Show current connection details"""
        print(f"Connected to: {self.host}:{self.port}")

    def do_quit(self, line):
        """Quit: Exit the CLI"""
        print("\nClosing Modbus connection...")
        if self.client:
            self.client.close()
        print("Goodbye!\n")
        return True

    # Aliases for quit command
    do_exit = do_quit
    do_q = do_quit


def main():
    """Main entry point with argument parsing"""
    parser = argparse.ArgumentParser(
        description='Wallbox Quasar Mock CLI - Control a Quasar mock Modbus server for V2G Liberty testing',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                                    # Connect to localhost:5020
  %(prog)s --host 192.168.1.100              # Connect to remote host
  %(prog)s -H localhost -p 5020              # Specify host and port
  %(prog)s -v                                # Enable verbose output

For V2G Liberty development: https://github.com/V2G-liberty/addon-v2g-liberty
        """
    )

    parser.add_argument(
        '-H', '--host',
        default='localhost',
        help='Modbus server host/IP address (default: localhost)'
    )

    parser.add_argument(
        '-p', '--port',
        type=int,
        default=5020,
        help='Modbus server port (default: 5020)'
    )

    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose output for debugging'
    )

    parser.add_argument(
        '--version',
        action='version',
        version=f'Wallbox Quasar Mock CLI v{__version__}'
    )

    args = parser.parse_args()

    # Create and run CLI
    try:
        cli = QuasarMockCLI(host=args.host, port=args.port, verbose=args.verbose)
        cli.cmdloop()
    except KeyboardInterrupt:
        print("\n\nInterrupted by user. Exiting...\n")
        sys.exit(0)
    except Exception as e:
        print(f"\n⚠️  Unexpected error: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
