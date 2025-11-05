#!/bin/bash

SCRIPT_DIR=$(cd $(dirname $0); pwd)
source $SCRIPT_DIR/../venv/bin/activate

SRCDIR=htdocs

# OUTDIR=../docs
OUTDIR=../../cuemol2_docs_site/docs

python pw_to_mkdown3.py \
       --source-dir $SRCDIR --output-dir $OUTDIR
