import { useEffect, useState } from "react";
import { api, type Filters } from "../api";
import { useAsync } from "../hooks";
import { fmtQty } from "../format";

const PAGE_SIZE = 100;

interface Props {
  filters: Filters;
  total: number; // all report lines for the date range
}

/** Scrollable on-screen preview of the Electronic General report, 100 rows per page. */
export function JournalPreview({ filters, total }: Props) {
  const [page, setPage] = useState(1);
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  // new date range -> back to the first page
  useEffect(() => setPage(1), [filters.start, filters.end]);

  const data = useAsync(
    (signal) => api.journalRows(filters, page, PAGE_SIZE, signal),
    [filters.start, filters.end, page],
    total > 0,
  );
  const rows = data.data?.rows ?? [];
  const columns = data.data?.columns ?? [];
  const text = new Set(data.data?.text_columns ?? []);
  const first = (page - 1) * PAGE_SIZE + 1;
  const last = Math.min(page * PAGE_SIZE, total);

  if (total === 0) return null;

  const pager = (
    <div className="pager" role="navigation" aria-label="Preview pages">
      <button type="button" onClick={() => setPage(1)} disabled={page === 1} aria-label="First page">
        «
      </button>
      <button type="button" onClick={() => setPage((p) => p - 1)} disabled={page === 1}>
        ‹ Previous
      </button>
      <span className="pager-info">
        Rows {fmtQty(first)}–{fmtQty(last)} of {fmtQty(total)} · page {page} of {pages}
      </span>
      <button type="button" onClick={() => setPage((p) => p + 1)} disabled={page >= pages}>
        Next ›
      </button>
      <button type="button" onClick={() => setPage(pages)} disabled={page >= pages} aria-label="Last page">
        »
      </button>
    </div>
  );

  return (
    <div className={`journal-preview${data.loading ? " loading" : ""}`}>
      <div className="preview-head">
        <h3>Preview</h3>
        {pager}
      </div>
      {data.error && (
        <div className="err" role="alert">
          Could not load preview: {data.error}
        </div>
      )}
      <div className="preview-scroll" tabIndex={0} aria-label="Report preview, scrollable">
        <table>
          <thead>
            <tr>
              <th className="num row-no">#</th>
              {columns.map((c) => (
                <th key={c} className={text.has(c) || c === "Transaction date" ? undefined : "num"}>
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={first + i} className={row[columns.indexOf("Line type")] === "Payment" ? "pay-line" : undefined}>
                <td className="num row-no">{first + i}</td>
                {row.map((v, j) =>
                  typeof v === "number" ? (
                    <td key={j} className="num">
                      {fmtQty(v)}
                    </td>
                  ) : (
                    <td key={j}>{v}</td>
                  ),
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {pages > 1 && <div className="preview-foot">{pager}</div>}
    </div>
  );
}
