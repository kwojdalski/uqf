// The TypeScript half of split_frontend.py (#2168): parses web/src files
// with the oxc parser rolldown ships (web/node_modules), and prints what the
// Python side needs as JSON. Run from web/, so the bare import resolves:
//
//   node ../scripts/dev/split_frontend_parse.mjs statements src/api.ts
//   node ../scripts/dev/split_frontend_parse.mjs imports src/App.tsx ...
//
// Offsets never leave this file: every piece of source is sent as its text,
// so the Python side cannot disagree with JavaScript about what an index
// counts (UTF-16 units here, code points there).
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

// Resolved from the working directory (web/), not from this file, which
// sits outside the package and so outside its node_modules.
const resolver = createRequire(join(process.cwd(), "package.json"));
const { parseSync } = await import(pathToFileURL(resolver.resolve("rolldown/experimental")).href);

const [mode, ...files] = process.argv.slice(2);

function parse(file) {
  const text = readFileSync(file, "utf8");
  const lang = file.endsWith(".tsx") ? "tsx" : "ts";
  const result = parseSync(file, text, { lang, sourceType: "module" });
  if (result.errors.length)
    throw new Error(`${file}: ${result.errors.map((e) => e.message).join("; ")}`);
  return { text, program: result.program };
}

// Keys that name a property, never a binding: `{ poll_seconds: number }`,
// `a.request`, `{ request: ... }` - not a use of a top-level `request`.
function isPropertyName(node, parent, key) {
  if (!parent) return false;
  const nonComputed = !parent.computed;
  switch (parent.type) {
    case "MemberExpression":
      return key === "property" && nonComputed;
    case "Property":
    case "PropertyDefinition":
    case "MethodDefinition":
    case "TSPropertySignature":
    case "TSMethodSignature":
    case "TSAbstractPropertyDefinition":
    case "TSAbstractMethodDefinition":
      return key === "key" && nonComputed && !(parent.shorthand && parent.type === "Property");
    case "TSQualifiedName":
      return key === "right";
    case "LabeledStatement":
    case "BreakStatement":
    case "ContinueStatement":
      return key === "label";
    default:
      return false;
  }
}

function walk(node, visit, parent = null, key = null) {
  if (!node || typeof node.type !== "string") return;
  visit(node, parent, key);
  for (const [k, value] of Object.entries(node)) {
    if (k === "parent") continue;
    if (Array.isArray(value)) {
      for (const child of value)
        if (child && typeof child === "object") walk(child, visit, node, k);
    } else if (value && typeof value === "object" && typeof value.type === "string") {
      walk(value, visit, node, k);
    }
  }
}

// Names a pattern binds: `const { a, b: [c] } = ...` binds a and c.
function patternNames(pattern, out) {
  if (!pattern) return;
  switch (pattern.type) {
    case "Identifier":
      out.add(pattern.name);
      break;
    case "ObjectPattern":
      for (const p of pattern.properties)
        patternNames(p.type === "RestElement" ? p.argument : p.value, out);
      break;
    case "ArrayPattern":
      for (const e of pattern.elements) patternNames(e, out);
      break;
    case "RestElement":
      patternNames(pattern.argument, out);
      break;
    case "AssignmentPattern":
      patternNames(pattern.left, out);
      break;
    case "TSParameterProperty":
      patternNames(pattern.parameter, out);
      break;
  }
}

// What a top-level statement declares, and whether it is a type only.
function declared(statement) {
  const decl =
    statement.type === "ExportNamedDeclaration" ? statement.declaration : statement;
  const exported = statement.type === "ExportNamedDeclaration";
  const names = new Set();
  let typeOnly = false;
  if (!decl) return { names: [], exported, typeOnly, kind: statement.type };
  switch (decl.type) {
    case "TSInterfaceDeclaration":
    case "TSTypeAliasDeclaration":
      typeOnly = true;
      names.add(decl.id.name);
      break;
    case "FunctionDeclaration":
    case "TSDeclareFunction":
    case "ClassDeclaration":
    case "TSEnumDeclaration":
      names.add(decl.id.name);
      break;
    case "VariableDeclaration":
      for (const d of decl.declarations) patternNames(d.id, names);
      break;
  }
  return { names: [...names], exported, typeOnly, kind: decl.type };
}

// Every name the statement reads that it does not bind itself.
function references(statement) {
  const used = new Set();
  const bound = new Set();
  walk(statement, (node, parent, key) => {
    if (node.type === "Identifier" && !isPropertyName(node, parent, key)) used.add(node.name);
    if (node.type === "VariableDeclarator" && parent !== statement.declaration && parent !== statement)
      patternNames(node.id, bound);
    if (
      node.type === "FunctionDeclaration" ||
      node.type === "FunctionExpression" ||
      node.type === "ArrowFunctionExpression"
    ) {
      for (const p of node.params) patternNames(p, bound);
      if (node !== statement && node !== statement.declaration && node.id) bound.add(node.id.name);
    }
    if (node.type === "CatchClause" && node.param) patternNames(node.param, bound);
    if (node.type === "TSTypeParameter") bound.add(node.name.name ?? node.name);
  });
  const own = new Set(declared(statement).names);
  return [...used].filter((n) => !bound.has(n) || own.has(n));
}

// The statement's end, carried to the end of its line when what follows on
// it is only a comment: `export const X = 1; // why` moves as one.
function lineEnd(text, end) {
  const newline = text.indexOf("\n", end);
  const rest = text.slice(end, newline === -1 ? text.length : newline);
  return /^\s*(\/\/.*)?$/.test(rest) ? (newline === -1 ? text.length : newline) : end;
}

function statements(file) {
  const { text, program } = parse(file);
  const out = [];
  let previous = 0;
  for (const statement of program.body) {
    const end = lineEnd(text, statement.end);
    const chunk = text.slice(previous, end);
    const lead = statement.start - previous;
    previous = end;
    const entry = { text: chunk, lead, type: statement.type, ...declared(statement) };
    if (statement.type === "ImportDeclaration") {
      entry.source = statement.source.value;
      entry.importKind = statement.importKind ?? "value";
      entry.specifiers = statement.specifiers.map((s) => ({
        imported: s.imported ? (s.imported.name ?? s.imported.value) : null,
        local: s.local.name,
        importKind: s.importKind ?? "value",
        type: s.type,
      }));
    } else {
      entry.refs = references(statement);
    }
    out.push(entry);
  }
  return { statements: out, tail: text.slice(previous) };
}

function imports(file) {
  const { text, program } = parse(file);
  const out = [];
  walk(program, (node) => {
    if (
      node.type === "ImportDeclaration" ||
      ((node.type === "ExportNamedDeclaration" || node.type === "ExportAllDeclaration") &&
        node.source)
    ) {
      out.push({
        type: node.type,
        text: text.slice(node.start, node.end),
        source: node.source.value,
        importKind: node.importKind ?? node.exportKind ?? "value",
        specifiers: (node.specifiers ?? []).map((s) => ({
          imported: s.imported ? (s.imported.name ?? s.imported.value) : null,
          local: (s.local ?? s.exported)?.name ?? null,
          importKind: s.importKind ?? s.exportKind ?? "value",
          type: s.type,
        })),
      });
    }
    if (node.type === "ImportExpression" && node.source?.type === "Literal")
      out.push({ type: "ImportExpression", text: text.slice(node.start, node.end), source: node.source.value, specifiers: [] });
  });
  return out;
}

const result = {};
for (const file of files) result[file] = mode === "statements" ? statements(file) : imports(file);
process.stdout.write(JSON.stringify(result));
