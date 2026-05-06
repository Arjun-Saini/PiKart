#!/bin/bash
cd /home/pi/PiKart
sleep 10
pigpiod
sleep 2
python /home/pi/PiKart/pikart_host.py