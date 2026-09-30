import { useState } from "react";
import { api, download, type Filters, type Kind, type Meta, type SlipRow } from "../lib/api";

interface Props {
  meta: Meta;
  filters: Filters;
  slips: SlipRow[];
  onPreview: (kind: Kind, store: string, date: string, downloadable: boolean) => void;
}

export function SlipsTable({ meta, filters, slips, onPreview }: Props) {
  const kinds = Object.keys(meta.kinds) as Kind[];
  const [zipKinds, setZipKinds] = useState<Kind[]>(kinds);
  const toggle = (k: Kind) =>
    setZipKinds((cur) => (cur.includes(k) ? cur.filter((x) => x !== k) : kinds.filter((x) => cur.includes(x) || x === k)));
  const hasOpenDay = slips.some((s) => !s.downloadable);
  const zipReady = slips.some((s) => s.downloadable);

  return (
    <section className="card">
      <div className="card-head">
        <div>
          <h2>Slips</h2>
          <div className="hint">
            {kinds.map((k) => `${k} ${meta.kinds[k].toLowerCase()}`).join(" · ")}. Click a type to preview, ↓ to
            download.{hasOpenDay && " Today's slips are preview-only until the day is closed."}
          </div>
        </div>
        <div className="zip-actions">
          <div className="kinds" role="group" aria-label="Slip types in ZIP">
            {kinds.map((k) => (
              <label key={k} title={meta.kinds[k]}>
                <input type="checkbox" checked={zipKinds.includes(k)} onChange={() => toggle(k)} />
                {k}
              </label>
            ))}
          </div>
          <button
            type="button"
            className="primary"
            disabled={!zipReady || !zipKinds.length}
            title={hasOpenDay ? "Today's slips are left out of the ZIP" : undefined}
            onClick={() => download(api.zipUrl(filters, zipKinds))}
          >
            Download all (ZIP){hasOpenDay && zipReady ? " · excl. today" : ""}
          </button>
        </div>
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Date</th>
              <th>Store</th>
              {kinds.map((k) => (
                <th key={k}>
                  {k} · {meta.kinds[k]}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {slips.length === 0 && (
              <tr>
                <td colSpan={2 + kinds.length} className="empty">
                  No slips for this filter
                </td>
              </tr>
            )}
            {slips.map((s) => (
              <tr key={`${s.date}-${s.store}`}>
                <td>
                  {s.date}
                  {!s.downloadable && <span className="open-day">Today · open</span>}
                </td>
                <td>{s.store}</td>
                {kinds.map((k) => (
                  <td key={k}>
                    {s[k] ? (
                      <span className="kind-cell">
                        <button
                          type="button"
                          className="ghost"
                          title={`Preview ${s.store}_${s.date}_${k}.txt`}
                          onClick={() => onPreview(k, s.store, s.date, s.downloadable)}
                        >
                          <span className="badge">{k}</span>
                        </button>
                        <button
                          type="button"
                          className="ghost"
                          disabled={!s.downloadable}
                          title={
                            s.downloadable
                              ? `Download ${s.store}_${s.date}_${k}.txt`
                              : "Today's slips can be downloaded from tomorrow"
                          }
                          aria-label={`Download ${s.store}_${s.date}_${k}.txt`}
                          onClick={() => download(api.slipUrl(k, s.store, s.date, true))}
                        >
                          ↓
                        </button>
                      </span>
                    ) : (
                      <span className="none">—</span>
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
