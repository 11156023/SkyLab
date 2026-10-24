// @vitest-environment happy-dom
import { act, useState } from "react";
import { createRoot } from "react-dom/client";
import { createColumnHelper, useTable } from "@tanstack/react-table";
import { afterEach, beforeEach, describe, expect, test } from "vitest";

import DataTable, { DataTableCells, DataTableHead } from "./DataTable";
import { baseFeatures, sortableFeatures } from "./features";

const columnHelper = createColumnHelper();

/* 欄位定義放模組層級：v9 的 FlexRender 把函式型的 cell 當元件掛載，render 裡新建會讓整格重新掛載 */
const columns = columnHelper.columns([
  columnHelper.accessor("name", {
    header: "Name",
    cell: ({ row }) => <strong>{row.original.name}</strong>,
    meta: { thClassName: "th", tdClassName: "td" },
  }),
  columnHelper.accessor("size", {
    header: () => <span>Size</span>,
    cell: (info) => info.getValue(),
    meta: {
      thClassName: "th thRight",
      tdClassName: (row) => (row.original.size > 10 ? "td big" : "td"),
      tdProps: (row) => ({ title: `size ${row.original.size}` }),
    },
  }),
  columnHelper.display({
    id: "actions",
    header: "",
    cell: ({ row, table, rowState }) => (
      <button type="button" onClick={() => table.options.meta?.onPick(row.original)}>
        {rowState?.busy ? "busy" : "pick"}
      </button>
    ),
  }),
]);

const ITEMS = [
  { id: 1, name: "alpha", size: 5 },
  { id: 2, name: "beta", size: 20 },
];

let host;
let root;
let picked;

function SimpleTable({ items = ITEMS, ...props }) {
  const table = useTable({
    features: baseFeatures,
    columns,
    data: items,
    getRowId: (item) => String(item.id),
    meta: { onPick: (item) => { picked = item; } },
  });
  return <DataTable table={table} className="table" {...props} />;
}

/* 列本身有互動狀態時：列元件擁有 state，自己畫 <tr>，用 DataTableCells 把狀態交給 cell */
function StatefulRow({ row, item }) {
  const [busy, setBusy] = useState(false);
  return (
    <tr data-row={item.id} onClick={() => setBusy(true)}>
      <DataTableCells row={row} rowState={{ busy }} />
    </tr>
  );
}

function RowRendererTable() {
  const table = useTable({ features: baseFeatures, columns, data: ITEMS, getRowId: (item) => String(item.id), meta: { onPick() {} } });
  return (
    <table>
      <DataTableHead table={table} />
      <tbody>
        {table.getRowModel().rows.map((row) => <StatefulRow key={row.id} row={row} item={row.original} />)}
      </tbody>
    </table>
  );
}

/* 模擬 formatDate：呼叫當下讀語系、cell 本身不訂閱語系（不呼叫 useTranslation） */
let currentLang = () => "";
const dateColumns = columnHelper.columns([
  columnHelper.accessor("at", {
    header: ({ lang }) => `When (${lang})`,
    cell: () => currentLang(),
  }),
]);
const DATE_ROWS = [{ id: "d1", at: "2026-10-22" }];

const sortColumns = columnHelper.columns([
  columnHelper.accessor("name", { header: "Name", cell: (info) => info.getValue() }),
]);

function SortedTable() {
  const table = useTable({
    features: sortableFeatures,
    columns: sortColumns,
    data: ITEMS.slice().reverse(),
    initialState: { sorting: [{ id: "name", desc: false }] },
  });
  return <DataTable table={table} />;
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  picked = null;
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe("DataTable", () => {
  test("renders the same markup as a hand-written table: thead/th classes, rows, td classes", async () => {
    await act(async () => { root.render(<SimpleTable />); });

    const table = host.querySelector("table.table");
    expect(table).not.toBeNull();
    const ths = [...table.querySelectorAll("thead th")];
    expect(ths.map((th) => th.className)).toEqual(["th", "th thRight", ""]);
    expect(ths.map((th) => th.textContent)).toEqual(["Name", "Size", ""]);

    const rows = [...table.querySelectorAll("tbody tr")];
    expect(rows).toHaveLength(2);
    expect(rows[0].querySelector("td strong").textContent).toBe("alpha");
    expect([...rows[1].querySelectorAll("td")].map((td) => td.className)).toEqual(["td", "td big", ""]);
    expect(rows[1].querySelectorAll("td")[1].getAttribute("title")).toBe("size 20");
  });

  test("table meta reaches the cells, and rowClassName/rowProps go on each <tr>", async () => {
    await act(async () => {
      root.render(<SimpleTable rowClassName={(row) => `tr r${row.id}`} rowProps={(row) => ({ "data-guide": `row-${row.id}` })} />);
    });
    const second = host.querySelector("tr.r2");
    expect(second.getAttribute("data-guide")).toBe("row-2");
    await act(async () => { second.querySelector("button").click(); });
    expect(picked).toEqual(ITEMS[1]);
  });

  test("colgroup, children (group rows), before/after (spacers) and bodyProps keep their DOM positions", async () => {
    await act(async () => {
      root.render(
        <SimpleTable
          colgroup={<colgroup><col className="c1" /></colgroup>}
          bodyProps={{ "data-virtualized": "true" }}
          before={<tr className="spacer-top" />}
          after={<tr className="spacer-bottom" />}
        >
          <tr className="group" />
        </SimpleTable>,
      );
    });
    const table = host.querySelector("table");
    expect(table.firstElementChild.tagName).toBe("COLGROUP");
    const tbody = table.querySelector("tbody");
    expect(tbody.getAttribute("data-virtualized")).toBe("true");
    expect([...tbody.children].map((tr) => tr.className)).toEqual(["group", "spacer-top", "", "", "spacer-bottom"]);
  });

  test("renderRow lets a stateful row component own its <tr> and pass state to the cells", async () => {
    await act(async () => { root.render(<RowRendererTable />); });
    const row = host.querySelector("tr[data-row='1']");
    expect(row.querySelector("button").textContent).toBe("pick");
    await act(async () => { row.click(); });
    expect(row.querySelector("button").textContent).toBe("busy");
    expect(host.querySelector("tr[data-row='2'] button").textContent).toBe("pick");
  });

  test("data changes re-render the rows", async () => {
    await act(async () => { root.render(<SimpleTable items={ITEMS} />); });
    await act(async () => { root.render(<SimpleTable items={[{ id: 3, name: "gamma", size: 1 }]} />); });
    const rows = host.querySelectorAll("tbody tr");
    expect(rows).toHaveLength(1);
    expect(rows[0].textContent).toContain("gamma");
  });

  test("sortableFeatures sorts rows client-side", async () => {
    await act(async () => { root.render(<SortedTable />); });
    expect([...host.querySelectorAll("tbody tr")].map((tr) => tr.textContent)).toEqual(["alpha", "beta"]);
  });

  test("switching language re-renders date cells even when the data did not change", async () => {
    const { default: i18n } = await import("i18next");
    const instance = i18n.createInstance();
    await instance.init({ lng: "en", resources: { en: {}, ja: {} } });
    currentLang = () => instance.language;
    const { I18nextProvider } = await import("react-i18next");

    function DateTable() {
      const table = useTable({ features: baseFeatures, columns: dateColumns, data: DATE_ROWS, getRowId: (r) => r.id });
      return <DataTable table={table} />;
    }
    await act(async () => { root.render(<I18nextProvider i18n={instance}><DateTable /></I18nextProvider>); });
    expect(host.querySelector("tbody td").textContent).toBe("en");
    await act(async () => { await instance.changeLanguage("ja"); });
    expect(host.querySelector("tbody td").textContent).toBe("ja");
    expect(host.querySelector("thead th").textContent).toBe("When (ja)");
  });

  test("cells re-render when only meta or columns change (data unchanged)", async () => {
    const metaColumns = columnHelper.columns([
      columnHelper.display({ id: "flag", header: "Flag", cell: ({ table }) => (table.options.meta.on ? "on" : "off") }),
    ]);
    const twoColumns = columnHelper.columns([
      ...metaColumns,
      columnHelper.display({ id: "extra", header: "Extra", cell: () => "x" }),
    ]);
    const STABLE = [{ id: "s1" }];
    function MetaTable({ on, wide }) {
      const table = useTable({
        features: baseFeatures,
        columns: wide ? twoColumns : metaColumns,
        data: STABLE,
        getRowId: (r) => r.id,
        meta: { on },
      });
      return <DataTable table={table} />;
    }
    await act(async () => { root.render(<MetaTable on={false} wide={false} />); });
    expect(host.querySelector("tbody td").textContent).toBe("off");

    await act(async () => { root.render(<MetaTable on wide={false} />); });
    expect(host.querySelector("tbody td").textContent).toBe("on");

    await act(async () => { root.render(<MetaTable on wide />); });
    expect(host.querySelectorAll("thead th")).toHaveLength(2);
    expect(host.querySelectorAll("tbody td")).toHaveLength(2);
  });

  test("hideHeader omits the thead", async () => {
    await act(async () => { root.render(<SimpleTable hideHeader />); });
    expect(host.querySelector("thead")).toBeNull();
    expect(host.querySelectorAll("tbody tr")).toHaveLength(2);
  });
});
