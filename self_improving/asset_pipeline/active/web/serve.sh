#!/usr/bin/env bash
cd "$(dirname "$0")"
exec "${ASSET_PIPELINE_PYTHON:-python3}" app.py
