#!/bin/bash
cd /home/pi/pikart
pigpiod
sleep 2

until ping -c1 -W1 192.168.50.1 > /dev/null 2>&1; do
    echo "Waiting for host..."
    sleep 1
done

python /home/pi/PiKart/pikart_client.py