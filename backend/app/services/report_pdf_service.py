"""


Uses SimpleDocTemplate + flowables (Table/Paragraph/Spacer/PageBreak/Image)
so pagination/overflow is handled by platypus's automatic flow — no
hand-rolled absolute positioning.

Security: all user-controlled text (test names, exception messages, file
names, error text, source lines) is escaped via xml.sax.saxutils.escape
before being passed into a Paragraph, since ReportLab's Paragraph markup
is XML-like and would otherwise misinterpret '<'/'&' in that text.
"""

from __future__ import annotations

import io
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.services.report_data_service import ReportData

_STATUS_COLORS = {
    "PASSED": colors.HexColor("#2e7d32"),
    "PASS": colors.HexColor("#2e7d32"),
    "FAILED": colors.HexColor("#c62828"),
    "FAIL": colors.HexColor("#c62828"),
    "ERROR": colors.HexColor("#c62828"),
    "SKIPPED": colors.HexColor("#9e9e9e"),
    "CANCELLED": colors.HexColor("#616161"),
}

_MAX_IMAGE_WIDTH = 420  # pt, fits within letter page margins


def _esc(value) -> str:
    if value is None:
        return ""
    return escape(str(value))


def _analysis_flowables(a, style) -> list:
    """Failure-analysis block; every user-derived string goes through _esc()."""
    out = [
        Paragraph(
            f"<b>Failure analysis:</b> {_esc(a.failure_title)} "
            f"({_esc(a.failure_category)}, confidence: {_esc(a.confidence)})",
            style,
        ),
        Paragraph(_esc(a.summary), style),
        Paragraph(f"<b>Likely cause (hypothesis, not confirmed):</b> {_esc(a.likely_cause)}", style),
        Paragraph("<b>Evidence (recorded facts):</b>", style),
    ]
    out += [Paragraph(f"&bull; <b>{_esc(e.label)}:</b> {_esc(e.value)}", style) for e in a.evidence]
    out.append(Paragraph("<b>Recommended actions (suggestions):</b>", style))
    out += [Paragraph(f"{i}. {_esc(x)}", style) for i, x in enumerate(a.recommended_actions, 1)]
    out.append(Spacer(1, 6))
    return out


def render_pdf_report(report: ReportData) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
        leftMargin=0.6 * inch,
        rightMargin=0.6 * inch,
    )

    styles = getSampleStyleSheet()
    title_style = styles["Title"]
    h2_style = styles["Heading2"]
    h3_style = styles["Heading3"]
    normal_style = styles["Normal"]
    error_style = ParagraphStyle(
        "ErrorText", parent=normal_style, textColor=colors.HexColor("#c62828")
    )

    story = []

    story.append(Paragraph("Synthetic Monitor Run Report", title_style))
    story.append(Spacer(1, 10))

    run = report.run
    project = report.project
    summary = report.summary

    status_color = _STATUS_COLORS.get(run.status, colors.black)
    status_style = ParagraphStyle(
        "StatusStyle", parent=h2_style, textColor=status_color
    )

    meta_lines = [
        f"<b>Project:</b> {_esc(project.name)} (#{project.id})",
        f"<b>Run:</b> #{run.id} &nbsp;&nbsp; <b>Browser:</b> {_esc(run.browser)}",
        f"<b>Created:</b> {_esc(run.created_at)}",
        f"<b>Started:</b> {_esc(run.started_at) if run.started_at else '—'}",
        f"<b>Completed:</b> {_esc(run.completed_at) if run.completed_at else '—'}",
        f"<b>Run duration:</b> {('%.2f' % run.duration_seconds) if run.duration_seconds is not None else '—'}s",
        f"<b>Cancel requested:</b> {run.cancel_requested}",
    ]
    for line in meta_lines:
        story.append(Paragraph(line, normal_style))

    story.append(Spacer(1, 6))
    story.append(Paragraph(f"Status: {_esc(run.status)}", status_style))
    story.append(Spacer(1, 14))

    # Summary table
    summary_data = [
        ["Total Tests", "Passed", "Failed", "Skipped", "Steps", "Screenshots", "Failures", "Duration (s)"],
        [
            summary.total_tests,
            summary.passed_count,
            summary.failed_count,
            summary.skipped_count,
            summary.total_steps,
            summary.screenshot_count,
            summary.failure_count,
            "%.2f" % summary.duration_seconds,
        ],
    ]
    summary_table = Table(summary_data, hAlign="LEFT")
    summary_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#404040")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ]
        )
    )
    story.append(summary_table)
    story.append(Spacer(1, 16))

    story.append(Paragraph(f"Tests ({len(report.tests)})", h2_style))
    story.append(Spacer(1, 8))

    for i, test in enumerate(report.tests):
        test_color = _STATUS_COLORS.get(test.status, colors.black)
        test_status_style = ParagraphStyle(
            "TestStatus", parent=h3_style, textColor=test_color
        )
        story.append(
            Paragraph(f"[{_esc(test.status)}] {_esc(test.nodeid)}", test_status_style)
        )
        duration_text = (
            "%.3fs" % test.duration_seconds if test.duration_seconds is not None else "—"
        )
        story.append(
            Paragraph(
                f"File: {_esc(test.file_name)} | Method: {_esc(test.method_name)} | Duration: {duration_text}",
                normal_style,
            )
        )

        if test.steps:
            step_data = [["#", "Step", "Status", "Duration (s)", "Error"]]
            for step in test.steps:
                step_data.append(
                    [
                        step.sequence_index,
                        Paragraph(_esc(step.display_name), normal_style),
                        step.status,
                        "%.3f" % step.duration_seconds if step.duration_seconds is not None else "—",
                        Paragraph(_esc(step.error) if step.error else "", normal_style),
                    ]
                )
            step_table = Table(
                step_data, hAlign="LEFT", colWidths=[25, 150, 50, 60, 170]
            )
            step_style_cmds = [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f0f0")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
            for row_idx, step in enumerate(test.steps, start=1):
                fill = _STATUS_COLORS.get(step.status)
                if step.status in ("FAIL",):
                    step_style_cmds.append(
                        ("BACKGROUND", (0, row_idx), (-1, row_idx), colors.HexColor("#fde8e8"))
                    )
            step_table.setStyle(TableStyle(step_style_cmds))
            story.append(step_table)
            story.append(Spacer(1, 6))

        if test.failure:
            f = test.failure
            a = test.analysis
            story.append(
                Paragraph(
                    f"<b>Exception:</b> {_esc(f.exception_type) if f.exception_type else 'Unknown'}",
                    error_style,
                )
            )
            if f.exception_message:
                story.append(
                    Paragraph(f"<b>Message:</b> {_esc(a.exception_message if a else f.exception_message)}", error_style)
                )
            if f.source_file:
                story.append(
                    Paragraph(
                        f"<b>Location:</b> {_esc(f.source_file)}:{f.source_line_number if f.source_line_number is not None else '?'}",
                        error_style,
                    )
                )
            if f.source_line:
                story.append(Paragraph(_esc(a.source_line if a else f.source_line), error_style))
            if f.raw_traceback:
                story.append(Paragraph(_esc(a.traceback if a and a.traceback else f.raw_traceback), error_style))
            story.append(Spacer(1, 6))

        if test.analysis:
            story.extend(_analysis_flowables(test.analysis, error_style))

        all_screenshots = list(test.screenshots)
        for step in test.steps:
            all_screenshots.extend(step.screenshots)

        for shot in all_screenshots:
            try:
                img = Image(io.BytesIO(shot.content))
                if img.drawWidth > _MAX_IMAGE_WIDTH:
                    scale = _MAX_IMAGE_WIDTH / float(img.drawWidth)
                    img.drawWidth *= scale
                    img.drawHeight *= scale
                story.append(img)
                story.append(Spacer(1, 6))
            except Exception:
                # Never let a corrupt/undecodable image break the whole report.
                story.append(Paragraph(f"[screenshot {_esc(shot.file_name)} could not be rendered]", normal_style))

        story.append(Spacer(1, 10))
        if i < len(report.tests) - 1:
            story.append(PageBreak())

    doc.build(story)
    return buffer.getvalue()
