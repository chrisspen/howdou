#!/bin/bash
set -e
VENV=.env
[ -d $VENV ] && rm -Rf $VENV || true
python3.9 -m venv $VENV
. $VENV/bin/activate
pip install -U pip wheel setuptools
pip install -r requirements.txt -r requirements-test.txt
