// Plotly stays out of the main bundle (#2043): read from the manifest the
// build writes, so the check is of what ships, not of how the source reads.
//
// Fails when anything the entry loads up front - the entry chunk and every
// chunk it imports statically, transitively - holds Plotly, or when Plotly is
// not in any chunk at all (the check would then pass on nothing).
import { readFileSync, statSync } from "node:fs";

const dist = new URL("../dist/", import.meta.url);
const manifest = JSON.parse(
  readFileSync(new URL(".vite/manifest.json", dist), "utf8"),
);
const PLOTLY = /plotly/i;
const holdsPlotly = (key) =>
  PLOTLY.test(key) || PLOTLY.test(manifest[key].file);

const entry = Object.keys(manifest).find((key) => manifest[key].isEntry);
const eager = new Set();
const visit = (key) => {
  if (eager.has(key)) return;
  eager.add(key);
  for (const next of manifest[key].imports ?? []) visit(next);
};
visit(entry);

const size = (key) => statSync(new URL(manifest[key].file, dist)).size;
const kib = (bytes) => `${(bytes / 1024).toFixed(1)} KiB`;
const leaked = [...eager].filter(holdsPlotly);
const lazy = Object.keys(manifest).filter(
  (key) => !eager.has(key) && holdsPlotly(key),
);
if (leaked.length) {
  console.error(`Plotly is in the main bundle: ${leaked.join(", ")}`);
  process.exit(1);
}
if (!lazy.length) {
  console.error("No chunk holds Plotly: the lazy import is not in the build.");
  process.exit(1);
}
console.log(
  `main bundle: ${[...eager].map((key) => `${manifest[key].file} ${kib(size(key))}`).join(", ")}`,
);
console.log(
  `Plotly, loaded on first plot: ${lazy.map((key) => `${manifest[key].file} ${kib(size(key))}`).join(", ")}`,
);
