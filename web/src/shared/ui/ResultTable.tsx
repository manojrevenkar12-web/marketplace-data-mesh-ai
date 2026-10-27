import { cell } from "./format";

export function ResultTable({ columns, rows, caption }: { columns: string[]; rows: (string | number | null)[][]; caption: string }) {
  return (
    <div className="scroll-x">
      <table className="data">
        <caption className="visually-hidden">{caption}</caption>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c} scope="col">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {r.map((v, j) => (
                <td key={j} className={typeof v === "number" ? "num" : undefined}>
                  {cell(v)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
