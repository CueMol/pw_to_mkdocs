#!/bin/bash

SCRIPT_DIR=$(cd $(dirname $0); pwd)
source $SCRIPT_DIR/../venv/bin/activate

(
    cd $SCRIPT_DIR/..
    mkdocs serve
)

