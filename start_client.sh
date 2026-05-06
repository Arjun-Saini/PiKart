#!/bin/bash
cd /home/pi/PiKart
sleep 15
pigpiod
sleep 2
python /home/pi/PiKart/pikart_client.py