#!/bin/bash
VENV=${VENV:-.env}
. $VENV/bin/activate
pylint --version
pylint --rcfile=pylint.rc howdou setup.py
