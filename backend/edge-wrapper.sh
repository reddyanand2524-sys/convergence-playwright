#!/bin/sh
# Same role as chrome-wrapper.sh, for Microsoft Edge: Edge refuses to start as root
# without --no-sandbox. Visible on the container's Xvfb display by default; set
# CHROME_HEADLESS=1 (shared switch) to run headless instead.
if [ "$CHROME_HEADLESS" = "1" ]; then
  EXTRA="--headless=new"
else
  EXTRA="--window-size=1366,768 --window-position=0,0"
fi
exec /opt/microsoft/msedge/msedge-real --no-sandbox --disable-gpu --disable-dev-shm-usage $EXTRA "$@"
