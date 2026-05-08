#!/bin/bash
# Wrapper - запускает serendipity_collect только в воскресенье
DAY=$(date +%w)
if [ "$DAY" != "0" ]; then
  echo "Not Sunday (day=$DAY), skip"
  exit 0
fi
set -a
. /volume1/homes/artemere-7601341/scripts/.env
set +a
python3 /volume1/homes/artemere-7601341/scripts/serendipity_collect.py
