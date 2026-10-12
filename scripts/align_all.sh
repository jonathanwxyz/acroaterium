#!/bin/sh
# Align every book's recording (recordings/Iliad/iliadNN.m4a) in turn. Takes ~4-5 hours on CPU,
# so run it detached:  setsid nohup scripts/align_all.sh > recordings/Iliad/align_all.log 2>&1 &
cd "$(dirname "$0")/.."
for b in $(seq 1 24); do
  f=recordings/Iliad/iliad$(printf %02d "$b").m4a
  [ -f "$f" ] || { echo "== book $b: $f missing, skipped"; continue; }
  echo "== book $b $(date +%T)"
  nice -n 10 .venv/bin/python scripts/align_audio.py --book "$b" --audio "$f" 2>&1 | grep -v -i -E 'warn|Loading weights|ret ='
done
echo "== all done $(date +%T)"
