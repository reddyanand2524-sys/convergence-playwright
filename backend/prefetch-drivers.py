"""Build-time only: resolve each installed browser's matching driver through Selenium
Manager (populating its cache), print the versions, and fail the build if a
Chromium driver's major version doesn't match its browser."""
import re
import subprocess
import sys

from selenium.webdriver.common.selenium_manager import SeleniumManager

ok = True
for name in ("chrome", "MicrosoftEdge", "firefox"):
    paths = SeleniumManager().binary_paths(["--browser", name])
    browser = subprocess.run([paths["browser_path"], "--version"], capture_output=True, text=True).stdout.strip()
    driver = subprocess.run([paths["driver_path"], "--version"], capture_output=True, text=True).stdout.strip().splitlines()[0]
    print(f"{name}: {browser} | {driver[:70]} | {paths['driver_path']}")
    if name != "firefox":
        if re.search(r"(\d+)\.", browser).group(1) != re.search(r"(\d+)\.", driver).group(1):
            print(f"MISMATCH: {name} browser/driver major versions differ")
            ok = False
sys.exit(0 if ok else 1)
