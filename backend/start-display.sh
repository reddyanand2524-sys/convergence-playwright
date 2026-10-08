#!/bin/sh
# Virtual display + VNC + noVNC so Selenium's Chrome can be watched from a browser.
rm -f /tmp/.X99-lock
Xvfb :99 -screen 0 1366x768x24 -nolisten tcp >/dev/null 2>&1 &
for i in 1 2 3 4 5 6 7 8 9 10; do [ -e /tmp/.X11-unix/X99 ] && break; sleep 0.5; done
x11vnc -display :99 -forever -shared -nopw -rfbport 5900 -localhost -quiet >/dev/null 2>&1 &
websockify --web /usr/share/novnc 6080 localhost:5900 >/dev/null 2>&1 &
exec "$@"
