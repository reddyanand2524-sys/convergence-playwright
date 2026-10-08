import { formatSeconds } from "../../utils/format.js";

export default function RunResultsSummary({ summary }) {
  const items = [
    { label: "Total tests", value: summary.total_tests },
    { label: "Passed", value: summary.passed_count, kind: "pass" },
    { label: "Failed", value: summary.failed_count, kind: "fail" },
    { label: "Skipped", value: summary.skipped_count, kind: "skip" },
    { label: "Exit code", value: summary.exit_code },
    { label: "Duration", value: formatSeconds(summary.duration_seconds) },
  ];

  return (
    <dl className="home-result-summary" aria-label="Execution summary">
      {items.map((item) => (
        <div key={item.label} className={item.kind ? `is-${item.kind}` : undefined}>
          <dt>{item.label}</dt>
          <dd>{item.value ?? "Not available"}</dd>
        </div>
      ))}
    </dl>
  );
}
