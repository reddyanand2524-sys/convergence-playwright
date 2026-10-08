"""

Jinja2 Environment is configured with EXPLICIT autoescaping (select_autoescape
over .html) so every {{ }} value (test names, exception messages, file
names, etc.) is HTML-escaped automatically — verified by a dedicated test
using a deliberately malicious test name containing a <script> tag.
"""

from __future__ import annotations

import base64
import os

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.services.report_data_service import ReportData

_TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")

_env = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR),
    autoescape=select_autoescape(["html", "htm", "xml"]),
)
# Explicit confirmation autoescaping is active for report.html (not relying
# on an implicit default): select_autoescape() enables escaping for any
# template name ending in .html/.htm/.xml, and report.html matches.
assert _env.autoescape("report.html") is True


def render_html_report(report: ReportData) -> str:
    template = _env.get_template("report.html")

    def _with_b64(shot):
        return {
            "id": shot.id,
            "file_name": shot.file_name,
            "mime_type": shot.mime_type,
            "capture_reason": shot.capture_reason,
            "content_b64": base64.b64encode(shot.content).decode("ascii"),
        }

    tests_ctx = []
    for test in report.tests:
        steps_ctx = []
        for step in test.steps:
            steps_ctx.append(
                {
                    "sequence_index": step.sequence_index,
                    "display_name": step.display_name,
                    "status": step.status,
                    "duration_seconds": step.duration_seconds,
                    "error": step.error,
                    "screenshots": [_with_b64(s) for s in step.screenshots],
                }
            )
        tests_ctx.append(
            {
                "id": test.id,
                "nodeid": test.nodeid,
                "file_name": test.file_name,
                "class_name": test.class_name,
                "method_name": test.method_name,
                "status": test.status,
                "duration_seconds": test.duration_seconds,
                "steps": steps_ctx,
                "screenshots": [_with_b64(s) for s in test.screenshots],
                "failure": test.failure,
            }
        )

    html = template.render(
        project=report.project,
        run=report.run,
        summary=report.summary,
        tests=tests_ctx,
    )
    return html
