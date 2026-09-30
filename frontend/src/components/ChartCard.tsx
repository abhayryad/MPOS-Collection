import { useState, type ReactNode } from "react";

export interface Column<T> {
  label: string;
  numeric?: boolean;
  value: (row: T) => ReactNode;
}

interface Props<T> {
  title: string;
  legend?: ReactNode;
  children: ReactNode;
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
}

/** Card with a chart and a toggleable table view of the same data. */
export function ChartCard<T>({ title, legend, children, columns, rows, rowKey }: Props<T>) {
  const [showTable, setShowTable] = useState(false);
  return (
    <section className="card">
      <div className="card-head">
        <h2>{title}</h2>
        <button type="button" className="ghost" onClick={() => setShowTable((s) => !s)} aria-expanded={showTable}>
          {showTable ? "Hide table" : "Table"}
        </button>
      </div>
      {legend}
      {children}
      {showTable && (
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                {columns.map((c) => (
                  <th key={c.label} className={c.numeric ? "num" : undefined}>
                    {c.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={rowKey(r)}>
                  {columns.map((c) => (
                    <td key={c.label} className={c.numeric ? "num" : undefined}>
                      {c.value(r)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
