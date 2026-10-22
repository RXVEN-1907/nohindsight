# killswitch_pi.md

Physical kill switch for the nohindsight broker, wired to a Raspberry Pi
Zero 2 W. The Python half is killswitch_pi.py in this directory.

## Pinout

gpiozero speaks BCM numbers. The header's physical pins are counted 1 to 40
down the odd side, starting at the SD card corner. Both are listed because
the silkscreen on the board shows neither, and counting wrong on a live
trading box is expensive. On the Pi, `pinout` prints this same mapping.

| Function              | BCM  | Physical pin | Wiring                                   |
| --------------------- | ---- | ------------ | ---------------------------------------- |
| Button (arm / reset)  | 17   | 11           | other leg to GND, pin 9                  |
| Crimson LED (latched) | 22   | 15           | 330 ohm series resistor, cathode to GND  |
| Sulfur LED (press)    | 27   | 13           | 330 ohm series resistor, cathode to GND  |
| Blue LED (armed)      | 4    | 7            | 330 ohm series resistor, cathode to GND  |
| Ground                | -    | 9            | button return                            |

Notes.

- The button uses the Pi's internal pull-up on BCM17, so no resistor is
  needed on it. Unpressed reads high, pressed ties the pin to GND.
- The palette maps to cheap parts as follows: lavender is the truth color,
  and a lavender LED is not a part you can buy in a bin, so the armed state
  shows blue or white. Crimson is the latch. Sulfur acknowledges a press in
  progress. An LED is an LED; the state names matter more than the wavelength.
- 330 ohm keeps LED current near 8mA on a 3.3V rail, comfortable for a GPIO
  pin and bright enough on a desk.

## Wiring sketch

```
              Pi Zero 2 W, header excerpt (SD card corner at top)

               3V3  o  1
                5V  o  2
              GPIO2 o  3
                5V  o  4
              GPIO3 o  5
               GND  o  6
   GPIO4  (blue) ---o  7     physical pin numbers on the right,
             GPIO14 o  8     BCM names in the table above
               GND  o  9
             GPIO15 o 10
  GPIO17 (button) --o 11
             GPIO18 o 12
  GPIO27 (sulfur) --o 13
               GND  o 14
  GPIO22 (crimson) -o 15
             GPIO23 o 16

  Wiring, outside the header:

    pin 11 (BCM17) ----/  /---- pin 9 (GND)   button, internal pull-up
    pin  7 (BCM4)  ---[330R]---|>|--- pin 14 (GND)   blue LED
    pin 13 (BCM27) ---[330R]---|>|--- pin 14 (GND)   sulfur LED
    pin 15 (BCM22) ---[330R]---|>|--- pin 14 (GND)   crimson LED
```

The sketch above numbers one header convention; if yours counts differently,
trust the BCM column of the table and the physical pin you can verify against
`pinout`, not the diagram.

## Behavior

- Hold the button 3 seconds: the switch arms, blue LED on.
- Short press while armed: stops the broker unit
  (`systemctl stop nohindsight-broker.service`).
- The risk guard on the Pi writes a state file, default
  `/run/nohindsight/killswitch.json`, containing `{"latched": true}` when a
  guard trips. The script polls it every 0.5 seconds. Latched: crimson LED
  on, everything else off.
- Hold 5 seconds while latched: reset. The script writes
  `{"latched": false}` back to the file. If the breach is still live, the
  guard relatches, and the crimson LED comes back. That is intended.
