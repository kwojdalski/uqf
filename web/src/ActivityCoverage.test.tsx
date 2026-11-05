/**
 * Every control has a stable activity name (#2000).
 *
 * The `interactions` level records a control by its `data-activity` name,
 * through one listener at the document (activity.ts). A control without a
 * name would be recorded as `unnamed` - so these fail on one, two ways:
 *
 *  - in the source: every <button>, <input>, <select>, <textarea>, <summary>
 *    and <a> a component writes carries the attribute, including the ones
 *    only a later state shows (a confirm dialog, an upload in flight);
 *  - on screen: every view is rendered, its controls revealed, and every
 *    element activity.ts's INTERACTIVE matches is checked - which catches a
 *    control a library renders, that no source line spells.
 */
import { readdirSync, readFileSync } from "node:fs";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import App from "./App";
import { INTERACTIVE } from "./activity";
import { mockWholeApi } from "./testing/activityFixtures";

/** What the server keeps as a target (activity._TARGET): a name, never text. */
const NAME = /^[A-Za-z0-9_.:/@ ,+-]{1,200}$/;
const TAGS = ["button", "input", "select", "textarea", "summary", "a"];

/** Each JSX opening tag of a control in `source`, as written: from `<tag` to
 *  the `>` that closes it outside any `{...}` expression or string. */
export function controlTags(
  source: string,
): { tag: string; line: number; text: string }[] {
  const found = [];
  const start = new RegExp(`<(${TAGS.join("|")})(?=[\\s>/])`, "g");
  for (const match of source.matchAll(start)) {
    let depth = 0;
    let quote = "";
    let end = match.index + match[0].length;
    for (; end < source.length; end++) {
      const c = source[end];
      if (quote) {
        if (c === quote) quote = "";
      } else if (c === "{") depth++;
      else if (c === "}") depth--;
      else if (depth === 0 && (c === '"' || c === "'")) quote = c;
      else if (depth === 0 && c === ">") break;
    }
    found.push({
      tag: match[1],
      line: source.slice(0, match.index).split("\n").length,
      text: source.slice(match.index, end + 1),
    });
  }
  return found;
}

it("finds a tag's end past the arrows and strings inside it", () => {
  const [tag] = controlTags(
    '<button title="a > b" onClick={() => go(x > 1)} data-activity="x">',
  );
  expect(tag.text.endsWith('data-activity="x">')).toBe(true);
});

it("names every control the source writes", () => {
  const dir = import.meta.dirname + "/";
  const unnamed: string[] = [];
  for (const file of readdirSync(dir)) {
    if (!file.endsWith(".tsx") || file.includes(".test.")) continue;
    for (const tag of controlTags(readFileSync(dir + file, "utf8")))
      if (!/\sdata-activity=/.test(tag.text))
        unnamed.push(`${file}:${tag.line} <${tag.tag}>`);
  }
  expect(unnamed).toEqual([]);
});

/** The one prefix every activity name a view writes shares, by its source:
 *  a file, or `file#Component` for a view App.tsx defines (#2110). The
 *  server's `?action=` filter matches a dotted prefix, so a view's submit
 *  event and its interactions must share one - `chart.draw` beside
 *  `charts.*` hid the row that says a chart was drawn from `?action=charts`.
 *  ControlView is not here on purpose: its submits carry the server's
 *  operation names (`process.*`, `backfill.start`), so the browser and the
 *  server row of one write share a name, while its controls are `control.*`. */
const AREAS: Record<string, string> = {
  "App.tsx#DeskView": "desk",
  "PlotPanel.tsx": "desk",
  "ChartsView.tsx": "charts",
  "FileBrowser.tsx": "files",
  "FileTable.tsx": "files",
  "FileDelete.tsx": "files",
  "UploadPanel.tsx": "files",
  "ActivityView.tsx": "activity",
  "CompareView.tsx": "compare",
  "ConfigHistory.tsx": "config",
  "SettingsView.tsx": "settings",
  "WorkspaceSelector.tsx": "workspace",
};

/** The activity names a source spells: each `data-activity` or `activity`
 *  prop and each `record(kind, name)`, as written - a template up to its
 *  first `${`, so `desk.show.${item}` is checked as `desk.show.`. */
export function activityNames(source: string): string[] {
  const quoted = String.raw`"([^"]*)"|\{\x60([^\x60$]*)`;
  const patterns = [
    new RegExp(String.raw`\b(?:data-)?activity=(?:${quoted})`, "g"),
    new RegExp(
      String.raw`\brecord\(\s*"[^"]*",\s*(?:"([^"]*)"|\x60([^\x60$]*))`,
      "g",
    ),
  ];
  return patterns.flatMap((pattern) =>
    [...source.matchAll(pattern)].map((m) => m[1] ?? m[2]),
  );
}

/** The source of `component` in `source`: from its `function` line to the
 *  next top-level declaration. */
function componentSource(source: string, component: string): string {
  const start = source.indexOf(`\nfunction ${component}(`);
  expect(start, `${component} is not defined`).toBeGreaterThan(-1);
  const end = source
    .slice(start + 1)
    .search(/\n(?:export )?(?:function|const) /);
  return end < 0 ? source.slice(start) : source.slice(start, start + 1 + end);
}

it("reads the names a source spells, templates by their fixed part", () => {
  expect(
    activityNames(
      'data-activity="a.b" activity="c.d" data-activity={`e.${x}`} ' +
        'record("submit", "f.g", {}) record("select", `h.${y}`)',
    ),
  ).toEqual(["a.b", "c.d", "e.", "f.g", "h."]);
});

it("gives every activity name a view writes its view's prefix", () => {
  const dir = import.meta.dirname + "/";
  const stray: string[] = [];
  for (const [where, prefix] of Object.entries(AREAS)) {
    const [file, component] = where.split("#");
    let source = readFileSync(dir + file, "utf8");
    if (component) source = componentSource(source, component);
    const names = activityNames(source);
    expect(names.length, `${where} spells no activity name`).toBeGreaterThan(0);
    for (const name of names)
      if (!name.startsWith(prefix + ".")) stray.push(`${where}: ${name}`);
  }
  expect(stray).toEqual([]);
});

/** Every control on screen without a usable name. */
function unnamedOnScreen(): string[] {
  return [...document.querySelectorAll(INTERACTIVE)]
    .filter((el) => !NAME.test(el.getAttribute("data-activity") ?? ""))
    .map((el) => el.outerHTML.slice(0, 120));
}

const settle = () => act(async () => new Promise((r) => setTimeout(r, 0)));

it("names every control every view renders, with a name the server keeps", async () => {
  mockWholeApi();
  render(<App />);
  const seen = new Set<string>();
  const check = async () => {
    await settle();
    expect(unnamedOnScreen()).toEqual([]);
    for (const el of document.querySelectorAll("[data-activity]"))
      seen.add(el.getAttribute("data-activity")!);
  };
  const views = screen
    .getByRole("navigation", { name: "Workspace views" })
    .querySelectorAll("button");
  expect(views.length).toBeGreaterThan(9);
  for (const view of views) {
    fireEvent.click(view);
    await check();
  }
  // the states a view only shows after a step
  fireEvent.click(screen.getByRole("button", { name: /Desk/ }));
  await screen.findByRole("button", { name: "Add filter" });
  fireEvent.click(screen.getByRole("button", { name: "Add filter" }));
  fireEvent.click(screen.getByLabelText(/Require complete publication/));
  await check();
  fireEvent.click(screen.getByRole("button", { name: /Control/ }));
  fireEvent.click(await screen.findByRole("button", { name: "stop" }));
  await check();
  fireEvent.click(screen.getByRole("button", { name: /Services/ }));
  fireEvent.click(await screen.findByRole("button", { name: "Browse prod-a" }));
  fireEvent.click(await screen.findByRole("button", { name: "releases/" }));
  await screen.findByRole("button", { name: "releases" });
  await check();
  // the ones the tests below drive by name, so a rename fails here first
  for (const name of [
    "nav.Desk",
    "desk.refine",
    "desk.tier",
    "desk.show.plot",
    "charts.choose",
    "charts.draw",
    "charts.param.dataset",
    "charts.param.hdb",
    "control.stop",
    "control.confirm",
    "files.sort.name",
    "files.select",
    "activity.export",
    "workspace.select",
  ])
    expect(seen).toContain(name);
});
