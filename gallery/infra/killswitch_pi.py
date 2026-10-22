#!/usr/bin/env python3
# killswitch_pi.py
# Physical kill switch for the nohindsight broker, on a Raspberry Pi Zero 2 W.
# Companion to killswitch_pi.md, which holds the pinout table and wiring.
#
# Behavior:
#   hold button 3s        arm the switch, blue LED on
#   short press, armed    stop the broker service
#   guard latches         crimson LED on, read from the state file
#   hold 5s, latched      reset: write latched=false back to the file
#
# Pin numbers below are BCM. gpiozero uses BCM by default. Physical pin
# numbers are what you count on the header, and they differ: BCM17 is
# physical pin 11, BCM22 is pin 15, BCM27 is pin 13, BCM4 is pin 7. On the
# Pi, run `pinout` to see both side by side. Mixing the two up is the
# classic way to wire a button to nothing.

import json
import os
import subprocess
import sys
import time

try:
    from gpiozero import Button, LED
except ImportError:
    sys.exit(
        "gpiozero is missing. On the Pi: sudo apt install python3-gpiozero"
    )

# BCM numbers. See killswitch_pi.md for the physical pin table.
PIN_BUTTON = 17       # BCM17, physical pin 11, internal pull-up to GND
PIN_LED_CRIMSON = 22  # BCM22, physical pin 15, latched state
PIN_LED_SULFUR = 27   # BCM27, physical pin 13, press acknowledgment
PIN_LED_BLUE = 4      # BCM4,  physical pin 7,  armed state

STATE_PATH = os.environ.get("NH_KILLSWITCH_STATE", "/run/nohindsight/killswitch.json")
BROKER_UNIT = "nohindsight-broker.service"

POLL_S = 0.5       # state file poll interval
HOLD_ARM_S = 3.0   # hold to arm
HOLD_RESET_S = 5.0 # hold to clear a latch


class KillSwitch:
    def __init__(self) -> None:
        self.button = Button(
            PIN_BUTTON,
            pull_up=True,
            bounce_time=0.05,
            hold_time=HOLD_ARM_S,
        )
        self.led_crimson = LED(PIN_LED_CRIMSON)
        self.led_sulfur = LED(PIN_LED_SULFUR)
        self.led_blue = LED(PIN_LED_BLUE)

        self.armed = False
        self.latched = False
        self.held_fired = False

        self.button.when_pressed = self._on_press
        self.button.when_held = self._on_held
        self.button.when_released = self._on_released

    # ---- state file ----------------------------------------------------

    def read_latched(self) -> bool:
        # The guard owns this file and may not have created it yet. A
        # missing or unreadable file reads as not latched; a corrupt file
        # too. The guard rewrites it every time the latch state changes.
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return bool(data.get("latched", False))
        except (OSError, ValueError):
            return False

    def write_latched(self, latched: bool) -> None:
        # Reset path only. Atomic replace so the guard never reads a half
        # file. If the breach is still live, the guard relatches and the
        # crimson LED returns. That is intended.
        tmp = STATE_PATH + ".tmp"
        try:
            state_dir = os.path.dirname(STATE_PATH)
            if state_dir:
                os.makedirs(state_dir, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"latched": latched}, fh)
            os.replace(tmp, STATE_PATH)
        except OSError as exc:
            print("cannot write state file: %s" % exc, file=sys.stderr)

    # ---- button --------------------------------------------------------

    def _on_press(self) -> None:
        self.held_fired = False

    def _on_held(self) -> None:
        self.held_fired = True
        if self.latched:
            # 5 second hold while latched: reset.
            self.write_latched(False)
            self.latched = False
            self.armed = False
        elif not self.armed:
            # 3 second hold: arm.
            self.armed = True
        self.button.hold_time = HOLD_RESET_S if self.latched else HOLD_ARM_S
        self.apply_leds()

    def _on_released(self) -> None:
        # Short press while armed, no hold fired: kill the broker.
        if self.armed and not self.held_fired:
            subprocess.run(["systemctl", "stop", BROKER_UNIT], check=False)
            self.armed = False
            self.apply_leds()

    # ---- LEDs ----------------------------------------------------------

    def apply_leds(self) -> None:
        if self.latched:
            self.led_crimson.on()
            self.led_blue.off()
            self.led_sulfur.off()
        elif self.armed:
            self.led_crimson.off()
            self.led_blue.on()
            self.led_sulfur.off()
        else:
            self.led_crimson.off()
            self.led_blue.off()
            # sulfur is handled in the poll loop while a press is in progress

    # ---- main loop -----------------------------------------------------

    def run(self) -> None:
        self.latched = self.read_latched()
        self.apply_leds()
        while True:
            now_latched = self.read_latched()
            if now_latched != self.latched:
                self.latched = now_latched
                self.button.hold_time = (
                    HOLD_RESET_S if self.latched else HOLD_ARM_S
                )
                self.apply_leds()
            # sulfur acknowledges a press in progress unless latched
            self.led_sulfur.value = self.button.is_pressed and not self.latched
            time.sleep(POLL_S)


if __name__ == "__main__":
    KillSwitch().run()
