import { api, download, type Meta } from "../lib/api";
import { useAsync, type AsyncState } from "../lib/hooks";
import { fmtQty } from "../lib/format";
import { FilterBar, useFilterState } from "../components/FilterBar";
import { JournalPreview } from "../components/JournalPreview";

/** Electronic General report: item + payment lines for all stores, downloaded as Excel. */
export function Reports({ meta }: { meta: AsyncState<Meta> }) {
  const { filters, preset, onChange, invalidRange } = useFilterState(meta.data);
  const summary = useAsync(
    (signal) => api.journalSummary(filters!, signal),
    [filters?.start, filters?.end, filters?.store],
    !!filters && !invalidRange,
  );
  const s = summary.data;
  const period = filters ? (filters.start === filters.end ? filters.start : `${filters.start} to ${filters.end}`) : "";

  const error = meta.error
    ? `Could not reach the database: ${meta.error}`
    : invalidRange
      ? "From date is after To date"
      : summary.error
        ? `Could not load report: ${summary.error}`
        : null;

  return (
    <div className={summary.loading ? "loading" : undefined}>
      <header className="page-head">
        <h1>Reports</h1>
        <div className="sub">{meta.data ? `${meta.data.database} on ${meta.data.server}` : "Connecting…"}</div>
      </header>

      {meta.data && filters && (
        <FilterBar
          meta={meta.data}
          filters={filters}
          preset={preset}
          onChange={onChange}
          onRefresh={summary.reload}
          refreshing={summary.loading}
        />
      )}
      {error && (
        <div className="err" role="alert">
          {error}
        </div>
      )}

      <section className="card report-card">
        <div className="card-head">
          <div>
            <h2>Electronic General</h2>
            <div className="hint">
              Every item line and payment line{filters?.store ? ` for ${filters.store}` : " for all stores"} · ITEM_WISE_TRANSACTIONS + PAYMENT_WISE_TRANSACTIONS,
              product name from DIM_PRODUCT
            </div>
          </div>
          <button
            type="button"
            className="primary"
            disabled={!filters || invalidRange || !s?.lines}
            onClick={() => filters && download(api.journalXlsxUrl(filters))}
          >
            ↓ Download Excel
          </button>
        </div>
        {s && (
          <div className="report-facts">
            <div>
              <span className="fact-value">{fmtQty(s.lines)}</span> lines
            </div>
            <div>
              <span className="fact-value">{fmtQty(s.receipts)}</span> receipts
            </div>
            <div>
              <span className="fact-value">{fmtQty(s.item_lines)}</span> item · {fmtQty(s.payment_lines)} payment
            </div>
            <div>
              <span className="fact-value">{s.stores.length}</span> {s.stores.length === 1 ? "store" : "stores"}
              {s.stores.length > 0 && <span className="hint"> ({s.stores.join(", ")})</span>}
            </div>
            <div className="hint">{period}</div>
          </div>
        )}
        {s && s.lines === 0 && <div className="empty">No transactions in this date range</div>}
        {s && filters && !invalidRange && <JournalPreview filters={filters} total={s.lines} />}
      </section>
    </div>
  );
}
