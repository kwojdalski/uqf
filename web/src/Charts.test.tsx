/**
 * The Charts view (#2044) holds no chart: it draws a form from the schema the
 * server lists, sends what the form holds, and renders the figure it is sent
 * as it was sent. Plotly is stubbed - what is under test is the figure handed
 * to it, not Plotly's drawing.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import type { Figure } from "./Plotly";

const drawn: { figure: Figure; name: string }[] = [];
vi.mock("./Plotly", () => ({
  PlotlyFigure: (props: { figure: Figure; name: string }) => {
    drawn.push(props);
    return <div data-testid="figure" />;
  },
}));
const { ChartsView } = await import("./ChartsView");

beforeEach(() => {
  drawn.length = 0;
});

const ok = (body: unknown) => ({ ok: true, json: async () => body });
const LIST = {
  charts: [
    {
      name: "markout_curve",
      title: "Markout curve by horizon",
      description: "Mean markout per horizon",
      point_limit: 600,
      params: {
        properties: {
          dataset: {
            type: "string",
            enum: ["fx_markout", "crypto_markout"],
            default: "fx_markout",
            title: "Dataset",
          },
          sym: { type: "string", default: "", title: "Symbol" },
        },
      },
    },
    {
      name: "ops_latency",
      title: "Query latency per process",
      description: "Completed query durations",
      point_limit: 5000,
      params: {
        properties: {
          limit: {
            type: "integer",
            default: 1000,
            minimum: 1,
            maximum: 5000,
            title: "Rows per process",
          },
        },
      },
    },
  ],
};
const FIGURE = {
  data: [{ type: "scatter", name: "LP1", x: [1, 5], y: [0.5, null] }],
  layout: { title: { text: "fx_markout by horizon" } },
};

function serve(answer: (path: string, body: unknown) => unknown) {
  const fetcher = vi.fn(async (path: string, options: RequestInit) =>
    path === "/charts"
      ? ok(LIST)
      : answer(path, JSON.parse((options?.body as string) ?? "null")),
  );
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}

it("draws a form from the schema, and renders the figure it is sent untouched", async () => {
  const fetcher = serve(() =>
    ok({
      name: "markout_curve",
      figure: FIGURE,
      point_limit: 600,
      points: 2,
      downsampled: true,
      truncated: true,
      notes: ["read cut at the row cap (5,000 rows)"],
    }),
  );
  render(<ChartsView />);
  const dataset = await screen.findByLabelText("Dataset");
  expect(dataset).toHaveAttribute("data-activity", "charts.param.dataset");
  fireEvent.change(dataset, { target: { value: "crypto_markout" } });
  fireEvent.change(screen.getByLabelText("Symbol"), {
    target: { value: "BTCUSD" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Draw" }));
  expect(await screen.findByTestId("figure")).toBeInTheDocument();
  const [path, options] = fetcher.mock.calls.at(-1)!;
  expect(path).toBe("/charts/markout_curve");
  expect(JSON.parse(options.body as string)).toEqual({
    dataset: "crypto_markout",
    sym: "BTCUSD",
  });
  expect(drawn.at(-1)).toEqual({ name: "charts.figure", figure: FIGURE });
  expect(
    screen.getByText("2 of up to 600 points · downsampled · truncated"),
  ).toBeInTheDocument();
  expect(
    screen.getByText("read cut at the row cap (5,000 rows)"),
  ).toBeInTheDocument();
});

it("sends a number field as a number, and shows the server's refusal", async () => {
  const fetcher = serve(() => ({
    ok: false,
    status: 422,
    json: async () => ({
      error: "ValidationFailed",
      detail: "usage logs need UQF_FRONTEND_PROCESS_CSV",
      transient: false,
    }),
  }));
  render(<ChartsView />);
  fireEvent.click(
    await screen.findByRole("button", { name: "Query latency per process" }),
  );
  fireEvent.change(screen.getByLabelText("Rows per process"), {
    target: { value: "2500" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Draw" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "usage logs need UQF_FRONTEND_PROCESS_CSV",
  );
  const [path, options] = fetcher.mock.calls.at(-1)!;
  expect(path).toBe("/charts/ops_latency");
  expect(JSON.parse(options.body as string)).toEqual({ limit: 2500 });
  await waitFor(() => expect(screen.queryByTestId("figure")).toBeNull());
});
