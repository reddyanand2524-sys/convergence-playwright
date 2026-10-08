/*
 * Plain-language status line for a run. Only states the backend actually
 * reports; no progress, counts or results.
 */
export default function RunStateMessage({ run, refreshError, stopped }) {
  const messages = {
    QUEUED: "This run is queued. Nothing has been executed yet.",
    RUNNING: "Execution started and is running on the backend.",
    COMPLETED: "Test execution completed.",
    FAILED: "Test execution failed.",
    CANCELLED: "This run was cancelled.",
  };

  // Cancel requested but the backend has not finished stopping the run yet.
  if (run.status === "RUNNING" && run.cancel_requested) {
    messages.RUNNING = "Cancellation requested. Waiting for the run to stop...";
  }

  const kind =
    run.status === "FAILED" ? "error" : run.status === "COMPLETED" ? "success" : "info";

  return (
    <>
      <p className={`home-notice is-${kind}`} role="status">
        {messages[run.status] || `Status: ${run.status}`}
      </p>
      {refreshError && <p className="home-notice is-error" role="alert">{refreshError}</p>}
      {stopped && (
        <p className="home-notice is-info" role="status">
          Automatic status refresh stopped. Reload the page to check the current status.
        </p>
      )}
    </>
  );
}
