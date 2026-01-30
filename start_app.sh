#!/bin/bash

# Navigate to the directory where this script is located
cd "$(dirname "$0")"

# Activate virtual environment
if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "Virtual environment not found! Creating one..."
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
fi

# Run the app
echo "Starting YouTube Link Extractor..."
echo "Open your browser at: http://127.0.0.1:5000"
python3 app.py
