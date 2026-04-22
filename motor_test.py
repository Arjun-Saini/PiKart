#!/usr/bin/env python3
"""Vibrating Motor Test

Wiring:
  GPIO 22 (Pin 15) -> 1k resistor -> NPN transistor Base
  Transistor Collector -> Motor (-)
  Motor (+) -> 3.3V or 5V
  Transistor Emitter -> GND
  Flyback diode across motor terminals
"""

import pigpio
import time

MOTOR = 22

pi = pigpio.pi()
if not pi.connected:
    print("Error: pigpiod not running, please run: sudo pigpiod")
    exit(1)

pi.set_mode(MOTOR, pigpio.OUTPUT)

print("=== Vibrating Motor Test (Ctrl+C to exit) ===\n")

try:
    # Test 1: On/Off control
    print("1. On/Off test: ON 3s -> OFF 2s -> ON 3s")
    pi.write(MOTOR, 1)
    print("   Motor ON")
    time.sleep(3)
    pi.write(MOTOR, 0)
    print("   Motor OFF")
    time.sleep(2)
    pi.write(MOTOR, 1)
    print("   Motor ON")
    time.sleep(3)
    pi.write(MOTOR, 0)
    print("   Motor OFF")
    time.sleep(1)

    # Test 2: Pulse vibration
    print("\n2. Pulse vibration test: 5 short bursts")
    for i in range(5):
        pi.write(MOTOR, 1)
        time.sleep(0.2)
        pi.write(MOTOR, 0)
        time.sleep(0.3)
        print(f"   Pulse {i+1}/5")

    print("\nTest complete!")

except KeyboardInterrupt:
    print("\nTest interrupted.")
finally:
    pi.set_PWM_dutycycle(MOTOR, 0)
    pi.write(MOTOR, 0)
    pi.stop()
