"""Rule tables for deterministic failure classification.

Pure data (no logic) so the classifier in failure_analysis_service.py stays
small. Wording rules:
  * LIKELY_CAUSES are hedged on purpose ("may", "often"): the recorded
    exception proves *what* failed, not *why*. They are never presented as
    confirmed facts.
  * RECOMMENDED_ACTIONS are troubleshooting suggestions, kept separate from
    evidence, and prefer explicit waits over fixed sleeps.
"""
from __future__ import annotations

import re

ELEMENT_NOT_FOUND = "ELEMENT_NOT_FOUND"
TIMEOUT = "TIMEOUT"
ELEMENT_NOT_INTERACTABLE = "ELEMENT_NOT_INTERACTABLE"
CLICK_INTERCEPTED = "CLICK_INTERCEPTED"
STALE_ELEMENT = "STALE_ELEMENT"
ASSERTION_FAILURE = "ASSERTION_FAILURE"
NAVIGATION_FAILURE = "NAVIGATION_FAILURE"
NETWORK_FAILURE = "NETWORK_FAILURE"
APPLICATION_ERROR = "APPLICATION_ERROR"
API_ERROR = "API_ERROR"
BROWSER_CONFIGURATION_ERROR = "BROWSER_CONFIGURATION_ERROR"
WEBDRIVER_ERROR = "WEBDRIVER_ERROR"
TEST_DATA_ERROR = "TEST_DATA_ERROR"
ENVIRONMENT_ERROR = "ENVIRONMENT_ERROR"
UNKNOWN = "UNKNOWN"

CATEGORIES = (
    ELEMENT_NOT_FOUND, TIMEOUT, ELEMENT_NOT_INTERACTABLE, CLICK_INTERCEPTED,
    STALE_ELEMENT, ASSERTION_FAILURE, NAVIGATION_FAILURE, NETWORK_FAILURE,
    APPLICATION_ERROR, API_ERROR, BROWSER_CONFIGURATION_ERROR, WEBDRIVER_ERROR,
    TEST_DATA_ERROR, ENVIRONMENT_ERROR, UNKNOWN,
)

TITLES = {
    ELEMENT_NOT_FOUND: "Element not found",
    TIMEOUT: "Timed out waiting",
    ELEMENT_NOT_INTERACTABLE: "Element not interactable",
    CLICK_INTERCEPTED: "Click intercepted by another element",
    STALE_ELEMENT: "Stale element reference",
    ASSERTION_FAILURE: "Assertion failed",
    NAVIGATION_FAILURE: "Navigation failed",
    NETWORK_FAILURE: "Network failure",
    APPLICATION_ERROR: "Application error",
    API_ERROR: "API error",
    BROWSER_CONFIGURATION_ERROR: "Browser could not be started",
    WEBDRIVER_ERROR: "WebDriver error",
    TEST_DATA_ERROR: "Test data error",
    ENVIRONMENT_ERROR: "Execution environment error",
    UNKNOWN: "Unclassified failure",
}

# Exception class (short name) -> category. The library defines these
# exceptions' meaning, so a match is strong evidence for the *category*.
TYPE_RULES: dict[str, str] = {
    "NoSuchElementException": ELEMENT_NOT_FOUND,
    "NoSuchShadowRootException": ELEMENT_NOT_FOUND,
    "NoSuchFrameException": ELEMENT_NOT_FOUND,
    "TimeoutException": TIMEOUT,
    "ElementNotInteractableException": ELEMENT_NOT_INTERACTABLE,
    "ElementNotVisibleException": ELEMENT_NOT_INTERACTABLE,
    "InvalidElementStateException": ELEMENT_NOT_INTERACTABLE,
    "ElementClickInterceptedException": CLICK_INTERCEPTED,
    "StaleElementReferenceException": STALE_ELEMENT,
    "AssertionError": ASSERTION_FAILURE,
    "InvalidArgumentException": NAVIGATION_FAILURE,
    "SessionNotCreatedException": BROWSER_CONFIGURATION_ERROR,
    "NoSuchDriverException": BROWSER_CONFIGURATION_ERROR,
    # Raised by the runner when a script's browser initialization cannot be adapted.
    "ConvergenceCompatibilityError": BROWSER_CONFIGURATION_ERROR,
    "InvalidSessionIdException": WEBDRIVER_ERROR,
    "NoSuchWindowException": WEBDRIVER_ERROR,
    "JavascriptException": APPLICATION_ERROR,
    "HTTPError": API_ERROR,
    "HTTPStatusError": API_ERROR,
    "ConnectionError": NETWORK_FAILURE,
    "ConnectTimeout": NETWORK_FAILURE,
    "ReadTimeout": NETWORK_FAILURE,
    "MaxRetryError": NETWORK_FAILURE,
    "NameResolutionError": NETWORK_FAILURE,
    "SSLError": NETWORK_FAILURE,
    "gaierror": NETWORK_FAILURE,
}

# Classes that say little on their own: only used after message patterns
# had their chance, and with lower confidence.
GENERIC_TYPE_RULES: dict[str, str] = {
    "WebDriverException": WEBDRIVER_ERROR,
    "Failed": ASSERTION_FAILURE,  # explicit pytest.fail(...)
    "KeyError": TEST_DATA_ERROR,
    "IndexError": TEST_DATA_ERROR,
    "ValueError": TEST_DATA_ERROR,
    "JSONDecodeError": TEST_DATA_ERROR,
    "UnicodeDecodeError": TEST_DATA_ERROR,
    "FileNotFoundError": TEST_DATA_ERROR,
    "ModuleNotFoundError": ENVIRONMENT_ERROR,
    "ImportError": ENVIRONMENT_ERROR,
    "PermissionError": ENVIRONMENT_ERROR,
    "MemoryError": ENVIRONMENT_ERROR,
    "OSError": ENVIRONMENT_ERROR,
}

# Message patterns -> category (checked in order, case-insensitive).
MESSAGE_RULES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (category, re.compile(pattern, re.I))
    for category, pattern in (
        (BROWSER_CONFIGURATION_ERROR,
         r"chrome instance exited|session not created|cannot find (chrome|microsoft edge|firefox) binary"
         r"|binary is not a firefox executable|only supports (chrome|edge) version|devtoolsactiveport"
         r"|unable to obtain driver|unable to locate or obtain driver"),
        (NETWORK_FAILURE,
         r"net::err_(internet_disconnected|name_not_resolved|connection_\w+|address_unreachable"
         r"|proxy_\w+|tunnel_\w+|ssl_\w+|cert_\w+|network_changed|timed_out)"
         r"|dns_probe_\w+|connection refused|name or service not known|max retries exceeded"),
        (NAVIGATION_FAILURE,
         r"net::err_(aborted|blocked_\w+|invalid_url|too_many_redirects|unsafe_\w+)"
         r"|reached error page|invalid argument: invalid 'url'|invalid url|unknown url scheme"),
        (WEBDRIVER_ERROR,
         r"disconnected: not connected to devtools|invalid session id|no such window"
         r"|target window already closed|chrome not reachable|cannot determine loading status"
         r"|tab crashed|session deleted|unexpected(ly)? exited|connection reset by peer"),
        (ENVIRONMENT_ERROR,
         r"cannot connect to x server|missing x server|no \$?display|no space left on device"
         r"|too many open files|cannot allocate memory|permission denied"),
        (API_ERROR, r"\b[45]\d\d (client|server) error\b"),
        (APPLICATION_ERROR,
         r"internal server error|bad gateway|service unavailable|gateway time-?out|http (error )?50\d"
         r"|uncaught (typeerror|referenceerror|syntaxerror)|application error"),
    )
)

LIKELY_CAUSES: dict[str, str] = {
    ELEMENT_NOT_FOUND: (
        "The locator may not match the page (it may be wrong or outdated), the element may not "
        "have rendered yet, or the page may be in a different state than the script expects "
        "(for example a different page, a frame, or the shadow DOM)."
    ),
    TIMEOUT: (
        "The condition the test was waiting for did not become true within the allowed time. "
        "Possible reasons: the wait condition does not match what the page does, the element or "
        "state never appeared, or the page was genuinely slow. The recorded evidence does not "
        "tell these apart."
    ),
    ELEMENT_NOT_INTERACTABLE: (
        "The element was found but could not be used in its current state, for example it may be "
        "hidden, disabled, off-screen, or not yet ready for input."
    ),
    CLICK_INTERCEPTED: (
        "Another element was covering the target at the moment of the click, for example an "
        "overlay, cookie banner, sticky header, or an animation still in progress."
    ),
    STALE_ELEMENT: (
        "The element was found earlier but the page updated before it was used, so the stored "
        "reference no longer points at an element in the current page."
    ),
    ASSERTION_FAILURE: (
        "A check in the test script evaluated to false. The recorded data shows that the "
        "assertion failed, but not why the page or data differed from what was expected."
    ),
    NAVIGATION_FAILURE: (
        "The browser could not load the requested page, for example because the address is "
        "invalid, the navigation was blocked or aborted, or the page returned an error page."
    ),
    NETWORK_FAILURE: (
        "The browser or the test could not reach the target host. Possible reasons: DNS "
        "resolution, a refused or reset connection, a proxy or certificate problem, or the "
        "target being down or unreachable from the test environment."
    ),
    APPLICATION_ERROR: (
        "The error text suggests a problem in the application under test (a server error or a "
        "script error on the page). It is a hint, not a confirmed application defect: the test "
        "itself could also have triggered it."
    ),
    API_ERROR: (
        "An HTTP call made by the test returned an error status. Whether the API or the request "
        "sent by the test is at fault cannot be determined from the recorded data alone."
    ),
    BROWSER_CONFIGURATION_ERROR: (
        "The selected browser could not be started or attached to its driver. Possible reasons: "
        "the browser or driver is missing or incompatible, or the browser exited during start-up."
    ),
    WEBDRIVER_ERROR: (
        "The WebDriver session or browser process failed or went away during the test (for "
        "example the browser crashed, was closed, or lost its connection)."
    ),
    TEST_DATA_ERROR: (
        "A Python data error occurred in the test script, such as a missing key, bad index, "
        "unparsable value or missing data file. It may indicate a data problem, but could also be "
        "a mistake in the script."
    ),
    ENVIRONMENT_ERROR: (
        "The test failed because of a problem with the execution environment (for example a "
        "missing Python module, a permission or resource problem), not necessarily the test logic."
    ),
    UNKNOWN: (
        "The recorded data does not match any known failure pattern, so no cause is proposed."
    ),
}

RECOMMENDED_ACTIONS: dict[str, tuple[str, ...]] = {
    ELEMENT_NOT_FOUND: (
        "Verify the locator against the page the browser actually showed (see the failure screenshot).",
        "Check that the element exists at that point: it may be inside an iframe or shadow DOM, or appear only after an action.",
        "Use an explicit wait for the element (WebDriverWait with expected_conditions) rather than a fixed sleep.",
        "Prefer stable locators (ids, data-* attributes) over position-based XPath or generated class names.",
    ),
    TIMEOUT: (
        "Check that the wait condition matches what the page does (presence, visibility and clickability are different conditions).",
        "Inspect the failure screenshot to see what the page showed when the wait expired.",
        "If the page is legitimately slow, raise the explicit wait timeout for that condition only, instead of adding sleeps.",
        "Confirm the awaited element or state can appear in the test environment.",
    ),
    ELEMENT_NOT_INTERACTABLE: (
        "Wait for the element to be visible and enabled (expected_conditions.element_to_be_clickable) before interacting.",
        "Scroll the element into view and check it is not hidden, disabled or inside a collapsed section.",
        "Check the failure screenshot to see whether the control is actually displayed.",
    ),
    CLICK_INTERCEPTED: (
        "Inspect the failure screenshot for an overlay, banner, modal or sticky element covering the target.",
        "Close or wait out the blocking element, then click once the target is clickable.",
        "Scroll the target into view so fixed headers or footers do not cover it.",
    ),
    STALE_ELEMENT: (
        "Locate the element again after the page update instead of reusing an old reference.",
        "Wait for the page to finish updating (for example expected_conditions.staleness_of, or the new element's visibility) before interacting.",
    ),
    ASSERTION_FAILURE: (
        "Compare the actual page state or value with what the assertion expects, using the failure screenshot and the assertion message.",
        "Check whether the expectation is still correct for the current application behaviour.",
        "Make sure the assertion runs after the page reached its final state (use an explicit wait where the value is set asynchronously).",
    ),
    NAVIGATION_FAILURE: (
        "Check the URL for typos, a missing scheme (https://) and redirects.",
        "Open the same address manually from the machine running the tests.",
        "Review the page-load behaviour of the target if the navigation was aborted or blocked.",
    ),
    NETWORK_FAILURE: (
        "Check that the target host is up and reachable from the machine or container running the tests (DNS, proxy, firewall).",
        "Check TLS certificates if the message mentions SSL or certificate errors.",
        "Retry manually from the test environment to see whether the problem is persistent or intermittent.",
    ),
    APPLICATION_ERROR: (
        "Reproduce the step manually and look at the application's response and browser console.",
        "Check the application's server logs for the time of this run.",
        "Confirm the test data and preconditions are valid before treating this as an application defect.",
    ),
    API_ERROR: (
        "Check the HTTP status code and response body of the failing call.",
        "Verify the request (URL, authentication, payload) the test sends.",
        "Check the API's own logs for the time of this run.",
    ),
    BROWSER_CONFIGURATION_ERROR: (
        "Check that the selected browser (Chrome, Edge or Firefox) is installed in the execution environment.",
        "Check that the browser and its driver versions are compatible.",
        "Review the platform's browser start-up logs for the cause of the exit.",
        "If the message mentions a Convergence compatibility problem, update the script's browser "
        "initialization as the message describes (Selenium 4 Options instead of desired_capabilities).",
    ),
    WEBDRIVER_ERROR: (
        "Check whether the browser crashed or was closed during the test (resource limits, a script closing the window).",
        "Make sure the script does not quit or close the driver before its last step.",
        "Re-run to see whether the problem is intermittent.",
    ),
    TEST_DATA_ERROR: (
        "Check the data the script reads (keys, indexes, files, formats) at the reported line.",
        "Add checks or defaults so missing data produces a clear failure message.",
    ),
    ENVIRONMENT_ERROR: (
        "Check that every module the script imports is available in the execution environment.",
        "Check file permissions and available resources in the execution environment.",
    ),
    UNKNOWN: (
        "Read the exception message and the failing line in the technical details.",
        "Open the failure screenshot, if any, to see the page state.",
        "Reproduce the failing step manually to narrow it down.",
    ),
}


def short_exception_name(exception_type: str | None) -> str:
    """'selenium.common.exceptions.NoSuchElementException' -> 'NoSuchElementException'."""
    if not exception_type:
        return ""
    return exception_type.strip().rsplit(".", 1)[-1].strip()
