/* Shows only the fields the backend returned; null fields are omitted. */
export default function FailureDiagnosticPanel({ failure }) {
  if (!failure) return null;

  const location = [
    failure.source_file,
    failure.source_line_number != null ? `Line ${failure.source_line_number}` : null,
  ]
    .filter(Boolean)
    .join(" - ");

  return (
    <section className="home-failure" aria-label="Failure details">
      <h5>Failure details</h5>

      {failure.exception_type && <p className="home-failure-type">{failure.exception_type}</p>}
      {failure.exception_message && <p className="home-failure-message">{failure.exception_message}</p>}
      {location && <p className="home-failure-location">{location}</p>}
      {failure.source_line && <pre className="home-failure-source">{failure.source_line}</pre>}

      {failure.raw_traceback && (
        <details className="home-failure-trace">
          <summary>View Traceback</summary>
          <pre className="cv-log" tabIndex={0} aria-label="Traceback">{failure.raw_traceback}</pre>
        </details>
      )}
    </section>
  );
}
