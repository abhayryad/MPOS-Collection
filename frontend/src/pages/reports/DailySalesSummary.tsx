import { api, download, type Meta } from "../../lib/api";
import { useAsync, type AsyncState } from "../../lib/hooks";
import { fmtAmount, fmtQty } from "../../lib/format";
import { FilterBar, useFilterState } from "../../components/FilterBar";

const TEXT = new Set(["store", "date", "terminal"]);
const COUNTS = new Set(["bills", "cn_count"]);
const fmt = (key: string, v: number) => (COUNTS.has(key) ? fmtQty(v) : fmtAmount(v));

/** Reports > Daily Sales Summary: one store on one day, one row per counter (terminal), plus Excel. */
export function DailySalesSummary({ meta }: { meta: AsyncState<Meta> }) {
  const { filters, preset, onChange, invalidRange, noDates } = useFilterState(meta.data, "latest");
  const day = filters?.start ?? "";
  const store = filters?.store ?? "";
  const ready = !!filters && !invalidRange && !noDates && filters.start === filters.end && !!store;
  const report = useAsync((signal) => api.dailySalesSummary(day, store, signal), [day, store], ready);
  const r = report.data;

  const error = meta.error
    ? `Could not reach the database: ${meta.error}`
    : noDates
      ? meta.data?.max_date
        ? "Pick a date"
        : "No sales data in the database yet"
      : filters && !store
        ? "Pick a store"
        : report.error
          ? `Could not load report: ${report.error}`
          : null;

  return (
    <div className={report.loading ? "loading" : undefined}>
      <header className="page-head">
        <div className="crumb">Reports ›</div>
        <h1>Daily Sales Summary</h1>
        <div className="sub">{meta.data ? `${meta.data.database} on ${meta.data.server}` : "Connecting…"}</div>
      </header>

      {meta.data && filters && (
        <FilterBar
          meta={meta.data}
          filters={filters}
          preset={preset}
          onChange={onChange}
          onRefresh={report.reload}
          refreshing={report.loading}
          storeDay
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
            <h2>Counter-wise sales</h2>
            <div className="hint">
              {store || "No store picked"} · {day} · bills from BILL_WISE_ORDER_DATA (cancelled left out), credit
              notes from CREDITMEMO payments
            </div>
          </div>
          <button
            type="button"
            className="primary"
            disabled={!ready || !r?.rows.length}
            onClick={() => download(api.dailySalesSummaryXlsxUrl(day, store))}
          >
            ↓ Download Excel
          </button>
        </div>
        {ready && r && r.rows.length === 0 && <div className="empty">No bills for this store on this day</div>}
        {ready && r && r.rows.length > 0 && (
          <div className="preview-scroll" tabIndex={0} aria-label="Daily sales summary">
            <table>
              <thead>
                <tr>
                  {r.columns.map((c) => (
                    <th key={c.key} className={TEXT.has(c.key) ? undefined : "num"}>
                      {c.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {r.rows.map((row) => (
                  <tr key={row.terminal}>
                    {r.columns.map((c) =>
                      TEXT.has(c.key) ? (
                        <td key={c.key}>{row[c.key]}</td>
                      ) : (
                        <td key={c.key} className="num">
                          {fmt(c.key, Number(row[c.key]))}
                        </td>
                      ),
                    )}
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <th scope="row" colSpan={3}>
                    Totals
                  </th>
                  {r.columns
                    .filter((c) => !TEXT.has(c.key))
                    .map((c) => (
                      <td key={c.key} className="num">
                        {fmt(c.key, r.totals[c.key] ?? 0)}
                      </td>
                    ))}
                </tr>
              </tfoot>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
