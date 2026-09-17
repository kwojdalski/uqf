import { readFileSync, readdirSync, existsSync } from "node:fs";
import { join } from "node:path";
import { expect, it } from "vitest";

/**
 * Does every dependency actually support the oldest Node we claim to?
 *
 * WHY THIS EXISTS. `jsdom@30` raised its floor from `^22.13.0` to
 * `^22.22.2`. Nothing caught it: npm's `engine-strict` is off by default so
 * `npm ci` only warns, and CI runs Node 24 where the warning never appears.
 * The only symptom was a contributor on Node 22.22.1 getting an EBADENGINE
 * they had to read carefully to understand - for a package that was, at that
 * moment, still working.
 *
 * That is the shape this repository keeps finding: a constraint that is real,
 * silent, and invisible to the machine that checks everything. So the floor
 * we advertise is asserted against the floors our dependencies advertise.
 *
 * Deliberately no semver dependency. The ranges in play are a handful of
 * `^x.y.z || ...` alternatives, and adding a package to check package
 * versions would be one more thing whose own engines could drift.
 */

interface Pkg {
  name?: string;
  engines?: { node?: string };
}

const root = join(import.meta.dirname, "..");
const self: Pkg & { devDependencies?: Record<string, string> } = JSON.parse(
  readFileSync(join(root, "package.json"), "utf8"),
);

/** `18`, `18.0` and `18.0.0` all mean the same floor. */
function parts(text: string): [number, number, number] | null {
  const m = /^(\d+)(?:\.(\d+))?(?:\.(\d+))?/.exec(text.trim());
  return m ? [Number(m[1]), Number(m[2] ?? 0), Number(m[3] ?? 0)] : null;
}

/** The lowest version each `^a.b.c` or `>=a.b.c` alternative admits. */
function caretFloors(range: string): [number, number, number][] {
  return range
    .split("||")
    .map((part) => part.trim())
    .flatMap((part) => {
      const v = parts(part.replace(/^[\^>=~\sv]+/, ""));
      return v ? [v] : [];
    });
}

function cmp(a: [number, number, number], b: [number, number, number]): number {
  for (let i = 0; i < 3; i += 1) if (a[i] !== b[i]) return a[i] - b[i];
  return 0;
}

function satisfies(version: [number, number, number], range: string): boolean {
  if (range.trim() === "*" || range.trim() === "") return true;
  return range
    .split("||")
    .map((p) => p.trim())
    .some((part) => {
      const floor = parts(part.replace(/^[\^>=~\sv]+/, ""));
      if (!floor) return false;
      // `^a.b.c` is also capped at the next major; `>=a.b.c` is not. Getting
      // that wrong in the lenient direction would make this test pass for a
      // dependency that refuses the floor.
      if (part.startsWith("^") && version[0] !== floor[0]) return false;
      return cmp(version, floor) >= 0;
    });
}

function installedEngines(): { name: string; range: string }[] {
  const modules = join(root, "node_modules");
  if (!existsSync(modules)) return [];
  const out: { name: string; range: string }[] = [];
  const consider = (dir: string, name: string) => {
    const manifest = join(dir, "package.json");
    if (!existsSync(manifest)) return;
    try {
      const pkg: Pkg = JSON.parse(readFileSync(manifest, "utf8"));
      const range = pkg.engines?.node;
      if (range) out.push({ name, range });
    } catch {
      /* a malformed manifest is npm's problem, not this test's */
    }
  };
  for (const entry of readdirSync(modules)) {
    if (entry.startsWith(".")) continue;
    if (entry.startsWith("@")) {
      const scope = join(modules, entry);
      for (const inner of readdirSync(scope))
        consider(join(scope, inner), `${entry}/${inner}`);
    } else {
      consider(join(modules, entry), entry);
    }
  }
  return out;
}

it("declares a Node range every installed dependency actually supports", () => {
  const declared = self.engines?.node;
  expect(declared, "web/package.json must declare engines.node").toBeTruthy();

  const floors = caretFloors(declared!);
  expect(
    floors.length,
    `could not parse any version floor out of ${declared}`,
  ).toBeGreaterThan(0);

  // Every floor we advertise must be a Node a contributor can actually use.
  // Checking the floors rather than the running version is the whole point:
  // CI runs one Node, and the floor is the one nobody tests on.
  const broken: string[] = [];
  for (const floor of floors) {
    for (const dep of installedEngines()) {
      if (!satisfies(floor, dep.range))
        broken.push(
          `${dep.name} requires ${dep.range}, so Node ${floor.join(".")} is unusable`,
        );
    }
  }
  expect(
    [...new Set(broken)],
    "engines.node advertises a Node some dependency refuses",
  ).toEqual([]);
});

it("parses the range forms this repository actually uses", () => {
  // The matcher is hand-rolled, so its own behaviour is pinned rather than
  // assumed - a silently-wrong satisfies() would make the test above pass
  // for every input.
  expect(satisfies([22, 13, 0], "^20.19.0 || ^22.13.0 || >=24.0.0")).toBe(true);
  expect(satisfies([22, 13, 0], "^22.22.2 || ^24.15.0 || >=26.0.0")).toBe(
    false,
  );
  expect(satisfies([22, 22, 2], "^22.22.2 || ^24.15.0 || >=26.0.0")).toBe(true);
  expect(satisfies([24, 0, 0], "^22.12.0 || ^24.0.0 || >=26.0.0")).toBe(true);
  expect(satisfies([26, 0, 0], ">=16.20.0")).toBe(true);
  expect(satisfies([22, 12, 0], "^22.13.0")).toBe(false);
  // Bare majors are the commonest form in the transitive tree, and the first
  // draft of this matcher parsed none of them - it demanded a.b.c and so
  // reported every one of 84 packages as refusing the floor.
  expect(satisfies([22, 13, 0], ">=18")).toBe(true);
  expect(satisfies([22, 13, 0], ">=22")).toBe(true);
  expect(satisfies([22, 13, 0], ">=24")).toBe(false);
  expect(satisfies([22, 13, 0], ">=10.0")).toBe(true);
  expect(satisfies([22, 13, 0], "*")).toBe(true);
  // `saxes` really does declare ">=v12.22.7". A leading `v` is legal and is
  // in this tree, so the matcher must not read it as an unparseable range -
  // which it silently treats as "refuses everything".
  expect(satisfies([22, 13, 0], ">=v12.22.7")).toBe(true);
});
