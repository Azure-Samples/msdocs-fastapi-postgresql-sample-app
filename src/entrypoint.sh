#!/bin/bash
set -e
python3 -m pip install --upgrade pip
python3 -m pip install -e src
python3 src/fastapi_app/seed_data.py
python3 -m gunicorn fastapi_app:app -c src/gunicorn.conf.py
