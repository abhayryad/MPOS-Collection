import { api, download, type Meta } from "../../lib/api";
import { useAsync, type AsyncState } from "../../lib/hooks";
import { fmtAmount, fmtQty } from "../../lib/format";
import { FilterBar, useFilterState } from "../../components/FilterBar";

/** Reports > Store Sale by Hour: one store on one day - receipts, sales amount and average sale per
 *  hour of payment, plus Excel. */
export function SaleByHour({ meta }: { meta: AsyncState<Meta> }) {
  const { filters, preset, onChange, invalidRange, noDates } = useFilterState(meta.data, "latest");
  const day = filters?.start ?? "";
  const store = filters?.store ?? "";
  const ready = !!filters && !invalidRange && !noDates && filters.start === filters.end && !!store;
  const report = useAsync((signal) => api.saleByHour(day, store, signal), [day, store], ready);
  const r = report.data;

  const error = meta.error
    ? `Could not reach the database: ${meta.error}`
    : noDates
      ? meta.data?.max_date
        ? "Pick a date"
        : "No sales data in the database yet"
      : invalidRange
        ? "From date is after To date"
        : filters && !store
          ? "Pick a store"
          : report.error
          ? `Could not load report: ${report.error}`
          : null;

  return (
    <div className={report.loading ? "loading" : undefined}>
      <header className="page-head">
        <div className="crumb">Reports ›</div>
        <h1>Store Sale by Hour</h1>
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
            <h2>Sale by hour</h2>
            <div className="hint">
              {store || "No store picked"} · {day} · hour = when the receipt was paid (PAYMENT_WISE_TRANSACTIONS);
              amount = item net incl. tax, returns deducted (ITEM_WISE_TRANSACTIONS)
            </div>
          </div>
          <button
            type="button"
            className="primary"
            disabled={!ready || !r?.rows.length}
            onClick={() => download(api.saleByHourXlsxUrl(day, store))}
          >
            ↓ Download Excel
          </button>
        </div>
        {ready && r && r.rows.length === 0 && <div className="empty">No transactions for this store on this day</div>}
        {ready && r && r.rows.length > 0 && (
          <div className="preview-scroll" tabIndex={0} aria-label="Sale by hour">
            <table>
              <thead>
                <tr>
                  <th className="num">Sales hour</th>
                  <th className="num">Number of transactions</th>
                  <th className="num">Sales amount</th>
                  <th className="num">Avg sales amount</th>
                </tr>
              </thead>
              <tbody>
                {r.rows.map((row) => (
                  <tr key={row.hour}>
                    <td className="num">{row.hour}</td>
                    <td className="num">{fmtQty(row.transactions)}</td>
                    <td className="num">{fmtAmount(row.amount)}</td>
                    <td className="num">{fmtAmount(row.avg)}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="totals-row">
                  <th scope="row">Totals</th>
                  <td className="num">{fmtQty(r.totals.transactions)}</td>
                  <td className="num">{fmtAmount(r.totals.amount)}</td>
                  <td />
                </tr>
              </tfoot>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
