import { expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Table } from "./App";

// The table is the only place a number or an instant is read closely, and
// until now it rendered whatever JSON carried: a rate as 1.10021000000000004,
// an instant with nine fractional digits. The catalog says how many decimals
// each column is shown with; these check that it is obeyed, and - as much as
// anything - that a column it says nothing about is left exactly as it was.

it("shows a float with the decimals its column declares", () => {
  render(<Table rows={[{ rate: 1.100213456 }]} decimals={{ rate: 5 }} />);
  expect(screen.getByText("1.10021")).toBeInTheDocument();
});

it("pads a short float out to its declared decimals", () => {
  // 1.1 and 1.10000 are the same number and a different column of figures:
  // the point of a fixed width is that the decimal points line up.
  render(<Table rows={[{ rate: 1.1 }]} decimals={{ rate: 5 }} />);
  expect(screen.getByText("1.10000")).toBeInTheDocument();
});

it("cuts an instant's fractional seconds to three by default", () => {
  render(
    <Table
      rows={[{ time: "2026-09-17T10:00:00.123456789" }]}
      decimals={{ time: 3 }}
    />,
  );
  expect(screen.getByText("2026-09-17T10:00:00.123")).toBeInTheDocument();
});

it("keeps an instant's timezone suffix", () => {
  // Trimmed textually rather than parsed: a round trip through Date would
  // move the instant into the browser's own timezone.
  render(
    <Table
      rows={[{ time: "2026-09-17T10:00:00.123456+01:00" }]}
      decimals={{ time: 3 }}
    />,
  );
  expect(screen.getByText("2026-09-17T10:00:00.123+01:00")).toBeInTheDocument();
});

it("pads an instant with no fractional part", () => {
  render(
    <Table rows={[{ time: "2026-09-17T10:00:00" }]} decimals={{ time: 3 }} />,
  );
  expect(screen.getByText("2026-09-17T10:00:00.000")).toBeInTheDocument();
});

it("drops the point entirely at zero decimals", () => {
  render(
    <Table rows={[{ time: "2026-09-17T10:00:00.9" }]} decimals={{ time: 0 }} />,
  );
  expect(screen.getByText("2026-09-17T10:00:00")).toBeInTheDocument();
});

it("leaves a column the catalog says nothing about exactly as it arrives", () => {
  // Every operational table in this app is this case: counts, states and
  // process names, which a decimal point would only damage.
  render(<Table rows={[{ sym: "EURUSD", rows_published: 1234 }]} />);
  expect(screen.getByText("EURUSD")).toBeInTheDocument();
  expect(screen.getByText("1234")).toBeInTheDocument();
});

it("leaves a symbol alone even when its column declares decimals", () => {
  // A declared width applies to what can carry one. Coercing a symbol would
  // turn EURUSD into NaN, which is worse than ignoring the setting.
  render(<Table rows={[{ sym: "EURUSD" }]} decimals={{ sym: 5 }} />);
  expect(screen.getByText("EURUSD")).toBeInTheDocument();
});

it("leaves a null as the placeholder rather than formatting it", () => {
  render(<Table rows={[{ rate: null }]} decimals={{ rate: 5 }} />);
  expect(screen.getByText("—")).toBeInTheDocument();
});

it("shows a timespan's nanoseconds as q does, at its declared decimals", () => {
  // #1329: the server sends nanoseconds. It sent an ISO duration cut to the
  // microsecond - 1234 ns as PT0.000001S - which the decimals never touched.
  render(
    <Table
      rows={[{ skew: 1234 }, { skew: 93_600_000_123_456 }, { skew: -1500 }]}
      decimals={{ skew: 9 }}
      types={{ skew: "timespan" }}
    />,
  );
  expect(screen.getByText("0D00:00:00.000001234")).toBeInTheDocument();
  expect(screen.getByText("1D02:00:00.000123456")).toBeInTheDocument();
  expect(screen.getByText("-0D00:00:00.000001500")).toBeInTheDocument();
});

it("cuts a timespan's fraction to three decimals by default", () => {
  render(
    <Table
      rows={[{ window: 1_500_000_000 }]}
      decimals={{ window: 3 }}
      types={{ window: "timespan" }}
    />,
  );
  expect(screen.getByText("0D00:00:01.500")).toBeInTheDocument();
});

it("shows an infinity as one, distinct from a missing value", () => {
  // #1330: 0w, -0w and 0W reached the table as null and showed as "—".
  render(
    <Table
      rows={[
        { mid: "inf", size: "-inf", horizon: "inf", sym: "inf", gap: null },
      ]}
      decimals={{ mid: 5, size: null, horizon: 3, sym: null, gap: 5 }}
      types={{
        mid: "float",
        size: "long",
        horizon: "timespan",
        sym: "symbol",
        gap: "float",
      }}
    />,
  );
  const cells = [...document.querySelectorAll("td")].map(
    (td) => td.textContent,
  );
  // A symbol spelled "inf" is a symbol, not an infinity.
  expect(cells).toEqual(["∞", "-∞", "∞", "inf", "—"]);
});
