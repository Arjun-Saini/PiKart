#!/bin/bash
cd /home/pi/PiKart
pigpiod
sleep 2

until ip addr show wlan0 | grep -q '192.168.50.1'; do
    echo "Waiting for wlan0..."
    sleep 1
done

python /home/pi/PiKart/pikart_host.py