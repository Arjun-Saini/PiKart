#!/usr/bin/env python3
"""Analog 2-axis Joystick + Select Button 测试程序

接线:
  MCP3008 CLK  -> GPIO 5
  MCP3008 MOSI -> GPIO 6
  MCP3008 MISO -> GPIO 13
  MCP3008 CS   -> GPIO 19
  Joystick VRx -> MCP3008 CH0
  Joystick VRy -> MCP3008 CH1
  Joystick SW  -> GPIO 17 (内部上拉,按下为低电平)
"""

import pigpio
import time

CLK  = 5
MOSI = 6
MISO = 13
CS   = 19
SW   = 17
DEADZONE = 8

pi = pigpio.pi()
if not pi.connected:
    print("错误:pigpiod 未启动，请先运行: sudo pigpiod")
    exit(1)

pi.set_mode(CLK, pigpio.OUTPUT)
pi.set_mode(MOSI, pigpio.OUTPUT)
pi.set_mode(MISO, pigpio.INPUT)
pi.set_mode(CS, pigpio.OUTPUT)
pi.set_mode(SW, pigpio.INPUT)
pi.set_pull_up_down(SW, pigpio.PUD_UP)

pi.write(CS, 1)
pi.write(CLK, 0)

DELAY = 0.00001  # 10微秒


def read_mcp3008(channel):
    pi.write(CS, 0)
    time.sleep(DELAY)
    cmd = [1, 1, (channel >> 2) & 1, (channel >> 1) & 1, channel & 1]
    for bit in cmd:
        pi.write(MOSI, bit)
        time.sleep(DELAY)
        pi.write(CLK, 1)
        time.sleep(DELAY)
        pi.write(CLK, 0)
        time.sleep(DELAY)
    # null bit
    pi.write(CLK, 1)
    time.sleep(DELAY)
    pi.write(CLK, 0)
    time.sleep(DELAY)
    # 读 10 位
    result = 0
    for _ in range(10):
        pi.write(CLK, 1)
        time.sleep(DELAY)
        result = (result << 1) | pi.read(MISO)
        pi.write(CLK, 0)
        time.sleep(DELAY)
    pi.write(CS, 1)
    return result


print("Joystick 测试启动 (Ctrl+C 退出)")
print("-" * 40)

try:
    while True:
        x_raw = read_mcp3008(0)
        y_raw = read_mcp3008(1)
        btn = pi.read(SW)

        x_pct = round((x_raw / 1023.0 - 0.5) * 200)
        y_pct = round((y_raw / 1023.0 - 0.5) * 200)
        if abs(x_pct) < DEADZONE:
            x_pct = 0
        if abs(y_pct) < DEADZONE:
            y_pct = 0

        dir_x = "←" if x_pct < -10 else "→" if x_pct > 10 else "·"
        dir_y = "↑" if y_pct < -10 else "↓" if y_pct > 10 else "·"
        btn_str = "按下" if btn == 0 else "--"

        print(f"\rX: {x_pct:+4d}% {dir_x}  Y: {y_pct:+4d}% {dir_y}  "
              f"RAW({x_raw:4d},{y_raw:4d})  BTN: {btn_str}   ",
              end="", flush=True)
        time.sleep(0.1)
except KeyboardInterrupt:
    print("\n退出测试。")
finally:
    pi.stop()

