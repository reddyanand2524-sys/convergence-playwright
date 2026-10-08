FROM python:3.14-slim-bookworm

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        gnupg \
        ca-certificates \
        unixodbc \
        unixodbc-dev \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
        | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
        > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y msodbcsql18 \
    && rm -rf /var/lib/apt/lists/*

# Chrome for Selenium runs. Uploaded tests call webdriver.Chrome() with no
# options, so a wrapper adds the flags required in a root/headless container.
COPY chrome-wrapper.sh /tmp/chrome-wrapper.sh
RUN curl -fsSL -o /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && apt-get update \
    && apt-get install -y --no-install-recommends /tmp/chrome.deb fonts-liberation fonts-dejavu-core fonts-inter fonts-noto-color-emoji xvfb x11vnc novnc websockify \
    && rm -rf /var/lib/apt/lists/* /tmp/chrome.deb \
    && mv /opt/google/chrome/chrome /opt/google/chrome/chrome-real \
    && mv /tmp/chrome-wrapper.sh /opt/google/chrome/chrome \
    && chmod +x /opt/google/chrome/chrome

# Microsoft Edge + Firefox ESR (Selenium runs). Edge gets the same root/container
# wrapper as Chrome. Both come from the package manager, so nothing is downloaded
# at run time.
COPY edge-wrapper.sh /tmp/edge-wrapper.sh
RUN echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/repos/edge stable main"         > /etc/apt/sources.list.d/microsoft-edge.list     && apt-get update     && apt-get install -y --no-install-recommends microsoft-edge-stable firefox-esr     && rm -rf /var/lib/apt/lists/*     && mv /opt/microsoft/msedge/msedge /opt/microsoft/msedge/msedge-real     && mv /tmp/edge-wrapper.sh /opt/microsoft/msedge/msedge     && chmod +x /opt/microsoft/msedge/msedge     && ln -s /usr/bin/firefox-esr /usr/local/bin/firefox

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# Pre-fetch the drivers matching the installed browsers into Selenium Manager's cache
# (the build fails if a Chromium driver doesn't match its browser), then forbid
# Selenium Manager from downloading anything at run time.
COPY prefetch-drivers.py /tmp/prefetch-drivers.py
RUN python /tmp/prefetch-drivers.py && rm /tmp/prefetch-drivers.py
ENV SE_OFFLINE=true     SE_AVOID_BROWSER_DOWNLOAD=true

COPY app ./app

COPY start-display.sh /usr/local/bin/start-display.sh
RUN chmod +x /usr/local/bin/start-display.sh
ENV DISPLAY=:99

EXPOSE 8000 6080

ENTRYPOINT ["/usr/local/bin/start-display.sh"]

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
