import { AlertTriangle, CheckCircle2, MinusCircle, RefreshCw, XCircle } from "lucide-react";

import { normalizeTestStatus } from "../../utils/status.js";

// Icon + text (never colour alone). Anything unknown is shown as-is, neutral.
const ICONS = {
  pass: CheckCircle2,
  fail: XCircle,
  error: AlertTriangle,
  skip: MinusCircle,
  flaky: RefreshCw,
  neutral: MinusCircle,
};

export default function ResultStatus({ status }) {
  const kind = normalizeTestStatus(status);
  const Icon = ICONS[kind];

  return (
    <span className={`home-result-status is-${kind}`}>
      <Icon size={14} aria-hidden="true" />
      {status}
    </span>
  );
}
