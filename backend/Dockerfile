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
    && apt-get install -y --no-install-recommends /tmp/chrome.deb fonts-liberation \
    && rm -rf /var/lib/apt/lists/* /tmp/chrome.deb \
    && mv /opt/google/chrome/chrome /opt/google/chrome/chrome-real \
    && mv /tmp/chrome-wrapper.sh /opt/google/chrome/chrome \
    && chmod +x /opt/google/chrome/chrome

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt


# Pre-fetch chromedriver so runs don't depend on network at execution time.
RUN python -c "from selenium.webdriver.common.selenium_manager import SeleniumManager as S; print(S().binary_paths(['--browser','chrome']))"

COPY app ./app

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
