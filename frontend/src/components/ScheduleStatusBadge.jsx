import { CheckCircle2, PauseCircle } from "lucide-react";

// Icon + text (not colour alone). Reuses the run badge styling.
export default function ScheduleStatusBadge({ enabled }) {
  const Icon = enabled ? CheckCircle2 : PauseCircle;

  return (
    <span className={`home-run-badge ${enabled ? "is-completed" : "is-cancelled"}`}>
      <Icon size={14} aria-hidden="true" />
      {enabled ? "Enabled" : "Disabled"}
    </span>
  );
}
