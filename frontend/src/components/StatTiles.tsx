import type { DashboardData } from "../api";
import { fmtMoney, fmtQty, fmtShort } from "../format";

export function StatTiles({ data }: { data: DashboardData }) {
  const t = data.tiles;
  const mops = data.codes.map((c) => `${c} ${fmtShort(data.mop_totals[c] ?? 0)}`).join(" · ");
  const tiles = [
    { label: "Net collection", value: fmtMoney(t.collection), note: mops },
    { label: "Bills", value: fmtQty(t.bills), note: "sale + return bills" },
    { label: "Items sold", value: fmtQty(t.items_sold), note: "qty on S slips" },
    { label: "Items returned", value: fmtQty(t.items_returned), note: "qty on R slips" },
    { label: "Cancelled bills", value: fmtQty(t.cancelled), note: "on C slips" },
  ];
  return (
    <section className="tiles" aria-label="Summary">
      {tiles.map((tile) => (
        <div className="tile" key={tile.label}>
          <div className="tile-label">{tile.label}</div>
          <div className="tile-value">{tile.value}</div>
          <div className="tile-note">{tile.note}</div>
        </div>
      ))}
    </section>
  );
}
