import { useMemo, useState } from "react";
import { api, type Kind, type Meta } from "../lib/api";
import { useAsync, type AsyncState } from "../lib/hooks";
import { SERIES, fmtAmount, fmtMoney, fmtQty, fmtShort } from "../lib/format";
import { BarChart, Legend, type Series } from "../components/BarChart";
import { ChartCard } from "../components/ChartCard";
import { FilterBar, useFilterState } from "../components/FilterBar";
import { SlipPreview, type SlipRef } from "../components/SlipPreview";
import { SlipsTable } from "../components/SlipsTable";
import { StatTiles } from "../components/StatTiles";

export function SalePostingDownload({ meta }: { meta: AsyncState<Meta> }) {
  const { filters, preset, onChange, invalidRange, noDates } = useFilterState(meta.data);
  const [preview, setPreview] = useState<SlipRef | null>(null);


  const dash = useAsync(
    (signal) => api.data(filters!, signal),
    [filters?.start, filters?.end, filters?.store],
    !!filters && !invalidRange && !noDates,
  );
  const data = dash.data;

  // Colour follows the payment method (fixed slot order from the full MOP list), never its rank.
  const colorOf = useMemo(
    () => Object.fromEntries((meta.data?.mop_codes ?? []).map((c, i) => [c, SERIES[i % SERIES.length]])),
    [meta.data],
  );

  const mopSeries: Series[] = useMemo(
    () =>
      (data?.codes ?? []).map((c) => ({
        key: c,
        label: c,
        color: colorOf[c] ?? SERIES[0],
        values: data!.collection.map((r) => Number(r[c] ?? 0)),
      })),
    [data, colorOf],
  );
  const itemSeries: Series[] = useMemo(
    () => [
      { key: "sold", label: "Sold", color: SERIES[0], values: (data?.items ?? []).map((r) => r.sold) },
      { key: "returned", label: "Returned", color: SERIES[1], values: (data?.items ?? []).map((r) => r.returned) },
    ],
    [data],
  );

  const refresh = () => {
    meta.reload();
    dash.reload();
  };
  const error = meta.error
    ? `Could not reach the database: ${meta.error}`
    : noDates
      ? meta.data?.max_date
        ? "Pick a date or range"
        : "No sales data in the database yet"
      : invalidRange
        ? "From date is after To date"
      : dash.error
        ? `Could not load data: ${dash.error}`
        : null;

  return (
    <div className={dash.loading ? "loading" : undefined}>
      <header className="page-head">
        <h1>Sale Posting Download</h1>
        <div className="sub">
          {meta.data
            ? `${meta.data.database} on ${meta.data.server} · ${
                meta.data.max_date ? `data ${meta.data.min_date} to ${meta.data.max_date}` : "no data yet"
              }`
            : "Connecting…"}
        </div>
      </header>

      {meta.data && filters && (
        <FilterBar
          meta={meta.data}
          filters={filters}
          preset={preset}
          onChange={onChange}
          onRefresh={refresh}
          refreshing={dash.loading}
        />
      )}

      {error && (
        <div className="err" role="alert">
          {error}
        </div>
      )}

      {data && meta.data && filters && (
        <>
          <StatTiles data={data} />

          <div className="grid-2">
            <ChartCard
              title="Daily collection by payment method"
              legend={<Legend series={mopSeries} />}
              rows={data.collection}
              rowKey={(r) => String(r.date)}
              columns={[
                { label: "Date", value: (r) => r.date },
                ...data.codes.map((c) => ({ label: c, numeric: true, value: (r: typeof data.collection[number]) => fmtAmount(Number(r[c] ?? 0)) })),
                {
                  label: "Net",
                  numeric: true,
                  value: (r) => fmtAmount(data.codes.reduce((t, c) => t + Number(r[c] ?? 0), 0)),
                },
              ]}
            >
              <BarChart
                categories={data.collection.map((r) => String(r.date))}
                series={mopSeries}
                mode="stack"
                format={fmtMoney}
                formatShort={fmtShort}
                totalLabel="Net"
              />
            </ChartCard>

            <ChartCard
              title="Items sold vs returned"
              legend={<Legend series={itemSeries} />}
              rows={data.items}
              rowKey={(r) => r.date}
              columns={[
                { label: "Date", value: (r) => r.date },
                { label: "Sold", numeric: true, value: (r) => fmtQty(r.sold) },
                { label: "Returned", numeric: true, value: (r) => fmtQty(r.returned) },
              ]}
            >
              <BarChart
                categories={data.items.map((r) => r.date)}
                series={itemSeries}
                mode="group"
                format={fmtQty}
                formatShort={fmtQty}
              />
            </ChartCard>
          </div>

          <SlipsTable
            meta={meta.data}
            filters={filters}
            slips={data.slips}
            onPreview={(kind, store, date, downloadable) => setPreview({ kind, store, date, downloadable })}
          />
        </>
      )}

      <SlipPreview
        slip={preview}
        kindName={(k: Kind) => meta.data?.kinds[k] ?? k}
        onClose={() => setPreview(null)}
      />
    </div>
  );
}
