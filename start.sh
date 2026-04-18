#!/usr/bin/env bash

echo "Starting Tor..."
tor -f ./torrc &

echo "Waiting for Tor..."
sleep 15

echo "Starting Python app..."
python server.py