// /charts (#2044) - models/charts.py.

/** One prepared chart (#2044): defined on the server, drawn here. */
export interface ChartInfo {
  name: string;
  title: string;
  description: string;
  point_limit: number;
  /** The JSON Schema the chart's request body must validate against. */
  params: ChartSchema;
}
export interface ChartList {
  charts: ChartInfo[];
}
/** A drawn chart: plotly.py's figure, and what was done to fit its limit. */
export interface ChartResult {
  name: string;
  figure: { data: unknown[]; layout?: Record<string, unknown> };
  point_limit: number;
  points: number;
  downsampled: boolean;
  truncated: boolean;
  notes: string[];
}
/** The part of a Pydantic model's JSON Schema a parameter form reads. */
export interface ChartSchema {
  properties: Record<string, ChartField>;
  required?: string[];
}
export interface ChartField {
  type?: "string" | "integer" | "number" | "boolean";
  enum?: string[];
  default?: string | number | boolean;
  title?: string;
  description?: string;
  minimum?: number;
  maximum?: number;
}
