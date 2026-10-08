import { CheckCircle2, Clock, Loader, Ban, XCircle } from "lucide-react";

import { normalizeRunStatus } from "../utils/status.js";

// Text label + icon (not colour alone).
const ICONS = {
  QUEUED: Clock,
  RUNNING: Loader,
  COMPLETED: CheckCircle2,
  FAILED: XCircle,
  CANCELLED: Ban,
  UNKNOWN: Clock,
};

export default function RunStatusBadge({ status }) {
  const Icon = ICONS[normalizeRunStatus(status)];

  return (
    <span className={`home-run-badge is-${String(status).toLowerCase()}`}>
      <Icon size={14} aria-hidden="true" />
      {status}
    </span>
  );
}
