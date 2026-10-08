#!/bin/sh
# Visible by default (renders on the container's virtual display, viewable via noVNC).
# Set CHROME_HEADLESS=1 to run without a display.
if [ "$CHROME_HEADLESS" = "1" ]; then
  EXTRA="--headless=new"
else
  EXTRA="--window-size=1366,768 --window-position=0,0"
fi
exec /opt/google/chrome/chrome-real --no-sandbox --disable-gpu --disable-dev-shm-usage $EXTRA "$@"
