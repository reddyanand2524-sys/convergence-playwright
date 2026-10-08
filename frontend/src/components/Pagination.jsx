import { ChevronLeft, ChevronRight } from "lucide-react";

/* Controls only; the parent fetches the requested page from the backend. */
export default function Pagination({ page, pageSize, total, totalPages, onChange, disabled }) {
  const pages = Math.max(totalPages, 1);
  const first = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const last = Math.min(page * pageSize, total);

  return (
    <nav className="home-pagination" aria-label="Run history pages">
      <span className="home-pagination-info">
        {total === 0 ? "No runs" : `Showing ${first}-${last} of ${total} runs`}
      </span>

      <div className="home-pagination-controls">
        <button
          className="home-secondary-button"
          type="button"
          disabled={disabled || page <= 1}
          onClick={() => onChange(page - 1)}
        >
          <ChevronLeft size={16} aria-hidden="true" />
          Previous
        </button>

        <span className="home-pagination-page" aria-current="page">
          Page {page} of {pages}
        </span>

        <button
          className="home-secondary-button"
          type="button"
          disabled={disabled || page >= pages}
          onClick={() => onChange(page + 1)}
        >
          Next
          <ChevronRight size={16} aria-hidden="true" />
        </button>
      </div>
    </nav>
  );
}
