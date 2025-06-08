#!/bin/bash

SCRIPT_DIR=$(cd $(dirname $0); pwd)
source $SCRIPT_DIR/../venv/bin/activate

SRCDIR=htdocs
OUTDIR=../docs

python pw_to_mkdown3.py \
       --source-dir htdocs --output-dir ../docs
