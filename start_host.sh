#!/bin/bash
cd /home/pi/PiKart
pigpiod
sleep 2

sudo iw dev p2p-dev-wlan0 del 2>/dev/null

sudo ip link set wlan0 down
sudo ip link set wlan0 up

sudo systemctl restart hostapd dnsmasq
sleep 2

python /home/pi/PiKart/pikart_host.py