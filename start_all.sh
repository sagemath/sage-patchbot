#!/bin/bash

SAGE=/levi/scratch/robertwb/buildbot/sage-4.6/sage
PYTHON="$SAGE -python"

mongod --port=21000 --dbpath=../data &> mongod.log &
exec $PYTHON serve.py --base=4.6.1 --port=21100

