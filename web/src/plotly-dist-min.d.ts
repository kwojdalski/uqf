// plotly.js-dist-min ships no types. react-plotly.js's factory takes the
// library as `unknown`, so nothing here needs more than its existence.
declare module "plotly.js-dist-min" {
  const Plotly: unknown;
  export default Plotly;
}
