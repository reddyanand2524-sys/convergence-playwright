from pathlib import Path

from app.services.pytest_diagnostics import (
    diagnose_failure,
    extract_exception_summary,
    parse_junit_report,
    parse_traceback_frames,
)


SELENIUM_TRACEBACK = """\
self = test_corlink.CorlinkTest object at 0x000001

    def test_corlink_flow(self):
        self.driver.get("https://corlinknetworks.com/")
        self.driver.find_element(By.LINK_TEXT, "Get in Touch").click()
>       self.driver.find_element(By.ID, "name").click()

test_corlink.py:14: in test_corlink_flow
    self.driver.find_element(By.ID, "name").click()
../.venv/Lib/site-packages/selenium/webdriver/remote/webelement.py:115: in click
    self._execute(Command.CLICK_ELEMENT)
../.venv/Lib/site-packages/selenium/webdriver/remote/webelement.py:395: in _execute
    return self._parent.execute(command, params)
E   selenium.common.exceptions.ElementClickInterceptedException: Message: element click intercepted: Element is not clickable at point (683, 745)
"""


def test_parse_traceback_frames_extracts_all_frames():
    frames = parse_traceback_frames(SELENIUM_TRACEBACK)

    assert len(frames) == 3
    assert frames[0].file == "test_corlink.py"
    assert frames[0].line == 14
    assert frames[0].func == "test_corlink_flow"
    assert frames[0].source == 'self.driver.find_element(By.ID, "name").click()'


def test_frame_is_user_frame_excludes_site_packages_and_conftest():
    frames = parse_traceback_frames(SELENIUM_TRACEBACK)

    assert frames[0].is_user_frame is True
    assert frames[1].is_user_frame is False
    assert frames[2].is_user_frame is False


def test_extract_exception_summary_finds_final_exception_line():
    exc_type, exc_message = extract_exception_summary(SELENIUM_TRACEBACK)

    assert exc_type == "selenium.common.exceptions.ElementClickInterceptedException"
    assert exc_message.startswith("Message: element click intercepted")


def test_diagnose_failure_picks_the_uploaded_test_frame_not_the_library_frame():
    frame, exc_type, exc_message = diagnose_failure(SELENIUM_TRACEBACK)

    assert frame.file == "test_corlink.py"
    assert frame.line == 14
    assert "find_element" in frame.source
    assert exc_type == "selenium.common.exceptions.ElementClickInterceptedException"
    assert exc_message.startswith("Message: element click intercepted")


def test_diagnose_failure_falls_back_to_last_frame_when_no_user_frame_found():
    traceback_text = """\
../.venv/Lib/site-packages/selenium/webdriver/remote/webelement.py:115: in click
    self._execute(Command.CLICK_ELEMENT)
E   selenium.common.exceptions.ElementClickInterceptedException: boom
"""

    frame, exc_type, exc_message = diagnose_failure(traceback_text)

    assert frame.file == "../.venv/Lib/site-packages/selenium/webdriver/remote/webelement.py"
    assert exc_type == "selenium.common.exceptions.ElementClickInterceptedException"
    assert exc_message == "boom"


def test_diagnose_failure_handles_empty_traceback():
    frame, exc_type, exc_message = diagnose_failure("")

    assert frame is None
    assert exc_type is None
    assert exc_message is None


JUNIT_XML_WITH_FAILURE = """\
<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" tests="1" failures="1">
    <testcase classname="test_corlink.CorlinkTest" name="test_corlink_flow" file="test_corlink.py" line="10" time="11.4">
      <failure message="ElementClickInterceptedException">{traceback}</failure>
    </testcase>
  </testsuite>
</testsuites>
""".format(traceback=SELENIUM_TRACEBACK)

JUNIT_XML_ALL_PASSED = """\
<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" tests="1" failures="0">
    <testcase classname="test_basic" name="test_basic" file="test_basic.py" line="1" time="0.01" />
  </testsuite>
</testsuites>
"""


def test_parse_junit_report_extracts_diagnostic_for_failing_test(tmp_path: Path):
    report_path = tmp_path / "report.xml"
    report_path.write_text(JUNIT_XML_WITH_FAILURE, encoding="utf-8")

    diagnostics = parse_junit_report(report_path)

    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.test_file == "test_corlink.py"
    assert diagnostic.test_class == "CorlinkTest"
    assert diagnostic.test_method == "test_corlink_flow"
    assert diagnostic.source_line_number == 14
    assert "find_element" in diagnostic.source_line
    assert diagnostic.exception_type == "selenium.common.exceptions.ElementClickInterceptedException"
    assert "element click intercepted" in diagnostic.exception_message


def test_parse_junit_report_returns_empty_list_when_all_passed(tmp_path: Path):
    report_path = tmp_path / "report.xml"
    report_path.write_text(JUNIT_XML_ALL_PASSED, encoding="utf-8")

    assert parse_junit_report(report_path) == []


def test_parse_junit_report_returns_empty_list_when_file_missing(tmp_path: Path):
    assert parse_junit_report(tmp_path / "does_not_exist.xml") == []


def test_diagnostic_format_includes_class_method_line_and_exception(tmp_path: Path):
    report_path = tmp_path / "report.xml"
    report_path.write_text(JUNIT_XML_WITH_FAILURE, encoding="utf-8")

    diagnostic = parse_junit_report(report_path)[0]
    formatted = diagnostic.format()

    assert "test_corlink.py::CorlinkTest::test_corlink_flow" in formatted
    assert "line 14" in formatted
    assert "ElementClickInterceptedException" in formatted


# --- Real-world traceback captured from an actual failing Selenium run in
# this environment (pytest 9.1.1 / Python 3.14 on Windows): a bare top-level
# `assert` failure. The JUnit testcase for this run has NO `file` attribute
# (confirmed: this pytest version does not emit one), and the test's own
# frame line has no "in <func>" suffix at all — just "<file>:<line>: " with
# nothing following on the same line, followed by a blank line and a
# truncation separator before the nested Selenium/site-packages frames.
REAL_BARE_ASSERT_TRACEBACK = """\
    def test_bad_click():
        options = webdriver.ChromeOptions()
        options.add_argument("--headless=new")
        driver = webdriver.Chrome(options=options)
        try:
            driver.get("https://example.com")
>           assert driver.find_element(By.ID, "nonexistent-element-xyz").is_displayed()
                   ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

..\\..\\AppData\\Local\\Temp\\diag_repro\\test_fail_demo2.py:10:
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _
.venv\\Lib\\site-packages\\selenium\\webdriver\\remote\\webdriver.py:914: in find_element
    return self.execute(Command.FIND_ELEMENT, {"using": by, "value": value})["value"]
.venv\\Lib\\site-packages\\selenium\\webdriver\\remote\\webdriver.py:506: in execute
    self.error_handler.check_response(response)

>       raise exception_class(message, screen, stacktrace)
E       selenium.common.exceptions.NoSuchElementException: Message: no such element: Unable to locate element: {"method":"css selector","selector":"[id="nonexistent-element-xyz"]"}

.venv\\Lib\\site-packages\\selenium\\webdriver\\remote\\errorhandler.py:232: NoSuchElementException
"""

REAL_JUNIT_XML_NO_FILE_ATTR = """\
<?xml version="1.0" encoding="utf-8"?>
<testsuites name="pytest tests">
  <testsuite name="pytest" errors="0" failures="1" skipped="0" tests="1">
    <testcase classname="test_fail_demo2" name="test_bad_click" time="5.5">
      <failure message="selenium.common.exceptions.NoSuchElementException">{traceback}</failure>
    </testcase>
  </testsuite>
</testsuites>
""".format(traceback=REAL_BARE_ASSERT_TRACEBACK)


def test_parse_traceback_frames_recognizes_bare_frame_with_no_in_func_suffix():
    frames = parse_traceback_frames(REAL_BARE_ASSERT_TRACEBACK)

    bare_frames = [f for f in frames if f.file.endswith("test_fail_demo2.py")]
    assert len(bare_frames) == 1
    assert bare_frames[0].line == 10
    assert bare_frames[0].func == ""


def test_parse_traceback_frames_recognizes_in_func_suffix_still_works():
    frames = parse_traceback_frames(SELENIUM_TRACEBACK)

    assert len(frames) == 3
    assert frames[0].func == "test_corlink_flow"


def test_bare_frame_for_uploaded_test_is_user_frame_despite_no_func():
    frames = parse_traceback_frames(REAL_BARE_ASSERT_TRACEBACK)
    test_frame = next(f for f in frames if f.file.endswith("test_fail_demo2.py"))

    assert test_frame.is_user_frame is True


def test_selenium_site_packages_frames_mixed_with_bare_test_frame_are_excluded():
    frames = parse_traceback_frames(REAL_BARE_ASSERT_TRACEBACK)
    non_user = [f for f in frames if "site-packages" in f.file.replace("\\", "/")]

    assert len(non_user) >= 1
    assert all(f.is_user_frame is False for f in non_user)


def test_diagnose_failure_picks_test_frame_even_though_it_appears_before_last_library_frame():
    """The exact failure mode being fixed: the uploaded test's own frame is
    FIRST in the traceback, and internal Selenium frames come LAST — the old
    "always pick the final frame" fallback would have picked the Selenium
    errorhandler.py frame. The fix must not simply reverse the bug and
    always pick the first frame either — it must pick the uploaded test's
    frame specifically because it is the one real user-code frame, by rule,
    not by position."""

    frame, exc_type, exc_message = diagnose_failure(REAL_BARE_ASSERT_TRACEBACK)

    assert frame is not None
    assert frame.file.endswith("test_fail_demo2.py")
    assert frame.line == 10
    assert "site-packages" not in frame.file.replace("\\", "/")
    assert exc_type == "selenium.common.exceptions.NoSuchElementException"
    assert exc_message.startswith("Message: no such element")


def test_diagnose_failure_recovers_assert_source_line_for_bare_frame():
    frame, _, _ = diagnose_failure(REAL_BARE_ASSERT_TRACEBACK)

    assert frame is not None
    assert "find_element" in frame.source
    assert "nonexistent-element-xyz" in frame.source


def test_parse_junit_report_with_no_file_attribute_derives_file_from_traceback(tmp_path: Path):
    report_path = tmp_path / "report.xml"
    report_path.write_text(REAL_JUNIT_XML_NO_FILE_ATTR, encoding="utf-8")

    diagnostics = parse_junit_report(report_path)

    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]

    # Must be the real uploaded test's basename, not a selenium/site-packages
    # path and not the dotted-classname-derived guess.
    assert diagnostic.test_file == "test_fail_demo2.py"
    assert "site-packages" not in diagnostic.test_file
    assert "selenium" not in diagnostic.test_file.lower()
    assert diagnostic.test_method == "test_bad_click"
    assert diagnostic.source_line_number == 10
    assert diagnostic.exception_type == "selenium.common.exceptions.NoSuchElementException"
    assert diagnostic.exception_message.startswith("Message: no such element")


def test_parse_junit_report_diagnostic_joins_to_result_persistence_matching_key(tmp_path: Path):
    """result_persistence_service.py matches a FailureDiagnostic to a
    TestResult via the exact key (file_name, class_name, method_name), where
    file_name is always reduced to Path(...).name first (its own JUnit
    parsing does this because pytest/plugin-derived file paths may be full
    run-temp-dir paths). The diagnostic's test_file must already be in that
    same normalized form or the join silently fails."""

    report_path = tmp_path / "report.xml"
    report_path.write_text(REAL_JUNIT_XML_NO_FILE_ATTR, encoding="utf-8")

    diagnostic = parse_junit_report(report_path)[0]

    # Simulate result_persistence_service._parse_junit_testcases's own
    # file_name normalization for this same classname/testcase, with no
    # `file` attribute either (the real-world case).
    classname = "test_fail_demo2"
    module_parts = classname.split(".")
    derived_file_name = ".".join(module_parts) + ".py"
    normalized_key_file = Path(derived_file_name).name

    assert diagnostic.test_file == normalized_key_file
