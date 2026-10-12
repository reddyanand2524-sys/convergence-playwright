"""Continue-after-error behavior of the Selenium compatibility runner and
the resulting JSON report, exercised through a real pytest subprocess."""

import json

from tests._report_harness import run_scenario

SIX_STEPS = '''
# 1 | open | https://shop.test/ |
self.driver.get("https://shop.test/")
# 2 | click | id=missing-login |
self.driver.find_element(By.ID, "missing-login").click()
# 3 | type | id=search | shoes
self.driver.find_element(By.ID, "search").send_keys("shoes")
# 4 | click | id=missing-go |
self.driver.find_element(By.ID, "missing-go").click()
# 5 | open | https://shop.test/results |
self.driver.get("https://shop.test/results")
# 6 | click | id=first-result |
self.driver.find_element(By.ID, "first-result").click()
'''


def by_number(report):
    return {s.stepNumber: s for s in report.steps}


def test_six_steps_with_failures_at_2_and_4_continue_to_the_end(tmp_path):
    report, events, shots, proc = run_scenario(tmp_path, SIX_STEPS)

    assert proc.returncode != 0  # pytest still reports the test as failed
    steps = by_number(report)
    assert sorted(steps) == [1, 2, 3, 4, 5, 6]  # all six represented
    assert [steps[n].status for n in range(1, 7)] == [
        "PASSED", "FAILED", "PASSED", "FAILED", "PASSED", "PASSED",
    ]
    assert all(s.attempted for s in steps.values())  # 3, 5, 6 were attempted
    for n in (2, 4):
        assert steps[n].error.type == "NoSuchElementException"
        assert steps[n].error.category == "LOCATOR_ERROR"
        assert steps[n].screenshot and steps[n].screenshot.startswith("screenshots/")
    assert report.status == "FAILED"
    s = report.summary
    assert (s.totalSteps, s.attemptedSteps, s.passedSteps, s.failedSteps, s.skippedSteps) == (6, 6, 4, 2, 0)
    assert s.totalErrors == 2 and s.totalPages == 2
    assert report.tests[0].status == "FAILED"
    # additive fields from the same real run
    assert s.continuationEnabled is True and s.failureCount == 2 and s.notRunSteps == 0
    assert report.metadata.executionEngine == "selenium" and report.metadata.testFile == "test_scenario.py"
    assert [e.level for e in report.executionLog].count("ERROR") == 2


def test_runner_does_not_stop_at_first_recoverable_error(tmp_path):
    _, events, _, _ = run_scenario(tmp_path, SIX_STEPS)
    steps_seen = [e["step"] for e in events]
    assert steps_seen[-1] == 6 and 5 in steps_seen  # ran past both failures


def test_stop_on_first_error_can_be_restored_via_env(tmp_path):
    report, _, _, _ = run_scenario(tmp_path, SIX_STEPS, env={"SELENIUM_COMPAT_CONTINUE_ON_ERROR": "0"})
    steps = by_number(report)
    assert steps[2].status == "FAILED"
    assert [steps[n].status for n in (3, 4, 5, 6)] == ["SKIPPED"] * 4
    assert steps[3].attempted is False and "unexpected error" in steps[3].skipReason
    assert report.status == "FAILED"
    assert report.summary.continuationEnabled is False and report.summary.notRunSteps == 4


def test_dependent_steps_are_skipped_while_independent_steps_continue(tmp_path):
    report, events, _, _ = run_scenario(tmp_path, '''
# 1 | open | https://shop.test/ |
self.driver.get("https://shop.test/")
# 2 | storeElement | id=missing-menu |
menu = self.driver.find_element(By.ID, "missing-menu")
# 3 | click | id=missing-menu |
menu.click()
# 4 | type | id=search | shoes
self.driver.find_element(By.ID, "search").send_keys("shoes")
''')
    steps = by_number(report)
    assert steps[2].status == "FAILED"
    assert steps[3].status == "SKIPPED" and steps[3].attempted is False
    assert "menu" in steps[3].skipReason and "step 2" in steps[3].skipReason
    assert steps[3].startTime is None and steps[3].executionTimeMs is None  # nothing fabricated
    assert steps[4].status == "PASSED"  # independent step ran
    s = report.summary
    assert (s.totalSteps, s.attemptedSteps, s.passedSteps, s.failedSteps, s.skippedSteps) == (4, 3, 2, 1, 1)
    assert report.status == "FAILED"  # failures are never hidden


def test_failed_assertion_is_recorded_and_does_not_end_the_test(tmp_path):
    report, _, _, _ = run_scenario(tmp_path, '''
# 1 | open | https://shop.test/ |
self.driver.get("https://shop.test/")
# 2 | assertTitle | | Wrong title
assert self.driver.title == "Wrong title"
# 3 | open | https://shop.test/next |
self.driver.get("https://shop.test/next")
''')
    steps = by_number(report)
    assert steps[2].status == "FAILED" and steps[2].error.category == "ASSERTION_FAILURE"
    assert steps[3].status == "PASSED"
    assert report.status == "FAILED"


def test_all_steps_pass(tmp_path):
    report, _, _, proc = run_scenario(tmp_path, '''
# 1 | open | https://shop.test/ |
self.driver.get("https://shop.test/")
# 2 | click | id=buy |
self.driver.find_element(By.ID, "buy").click()
# 3 | assertTitle | | Title of https://shop.test/
assert self.driver.title == "Title of https://shop.test/"
''')
    assert proc.returncode == 0
    assert report.status == "PASSED"
    assert (report.summary.totalSteps, report.summary.passedSteps, report.summary.failedSteps) == (3, 3, 0)
    assert report.errors == []
    assert all(s.startTime and s.completionTime and s.executionTimeMs is not None for s in report.steps)


def test_execution_stops_before_all_defined_steps_run(tmp_path):
    report, _, _, _ = run_scenario(tmp_path, '''
# 1 | open | https://shop.test/ |
self.driver.get("https://shop.test/")
# 2 | click | id=buy |
self.driver.find_element(By.ID, "buy").click()
return
# 3 | click | id=pay |
self.driver.find_element(By.ID, "pay").click()
# 4 | click | id=done |
self.driver.find_element(By.ID, "done").click()
''')
    steps = by_number(report)
    assert [steps[n].status for n in (1, 2, 3, 4)] == ["PASSED", "PASSED", "SKIPPED", "SKIPPED"]
    assert steps[3].attempted is False and steps[3].skipReason.startswith("Not executed")
    s = report.summary
    assert s.totalSteps == 4 and s.attemptedSteps == 2 and s.skippedSteps == 2


def test_multiple_pages_are_grouped_by_url(tmp_path):
    report, _, _, _ = run_scenario(tmp_path, '''
# 1 | open | https://a.test/ |
self.driver.get("https://a.test/")
# 2 | open | https://b.test/ |
self.driver.get("https://b.test/")
# 3 | click | id=x |
self.driver.find_element(By.ID, "x").click()
''')
    assert [p.url for p in report.pages] == ["https://a.test/", "https://b.test/"]
    assert [p.pageId for p in report.pages] == ["PAGE-001", "PAGE-002"]
    assert [len(p.events) for p in report.pages] == [1, 2]
    assert report.summary.totalPages == 2
    assert [p.pageTitle for p in report.pages] == ["Title of https://a.test/", "Title of https://b.test/"]


def test_screenshot_failure_keeps_the_test_failure_and_the_report(tmp_path):
    report, _, shots, _ = run_scenario(tmp_path, '''
# 1 | open | https://shop.test/ |
self.driver.get("https://shop.test/")
# 2 | click | id=missing-x |
self.driver.find_element(By.ID, "missing-x").click()
''', env={"FAKE_SHOT_FAIL": "1"})
    steps = by_number(report)
    assert shots == set()
    assert steps[2].status == "FAILED" and steps[2].screenshot is None  # no screenshot claimed
    assert report.status == "FAILED" and report.artifacts.screenshots == []


def test_browser_initialisation_failure_reports_every_defined_step_as_skipped(tmp_path):
    report, events, _, _ = run_scenario(
        tmp_path,
        '''
# 1 | open | https://shop.test/ |
self.driver.get("https://shop.test/")
# 2 | click | id=buy |
self.driver.find_element(By.ID, "buy").click()
''',
        setup='raise SessionNotCreatedException("Message: session not created: cannot find Chrome binary")',
    )
    assert report.status == "FAILED"
    assert [s.status for s in report.steps] == ["SKIPPED", "SKIPPED"]
    assert all(not s.attempted and "setup failed" in s.skipReason for s in report.steps)
    assert report.errors[0].category == "BROWSER_INIT_ERROR"
    assert report.summary.attemptedSteps == 0 and report.summary.skippedSteps == 2


def test_password_values_never_reach_the_report(tmp_path):
    report, events, _, _ = run_scenario(tmp_path, '''
# 1 | open | https://shop.test/login |
self.driver.get("https://shop.test/login")
# 2 | type | id=password | Sup3rSecret!
self.driver.find_element(By.ID, "password").send_keys("Sup3rSecret!")
''')
    text = report.model_dump_json() + json.dumps(events)
    assert "Sup3rSecret!" not in text
    assert by_number(report)[2].status == "PASSED"
