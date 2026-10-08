#!/bin/sh
exec /opt/google/chrome/chrome-real --no-sandbox --headless=new --disable-gpu --disable-dev-shm-usage "$@"
