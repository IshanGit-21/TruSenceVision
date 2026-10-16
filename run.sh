#!/bin/bash
# TruSenceVision — Run Script
# Double-click this file or run: bash run.sh

echo "Starting TruSenceVision..."

# Kill anything on port 5001 first (clears stuck sessions)
lsof -ti:5001 | xargs kill -9 2>/dev/null
sleep 1

# Run with Anaconda Python
/opt/anaconda3/bin/python app.py
