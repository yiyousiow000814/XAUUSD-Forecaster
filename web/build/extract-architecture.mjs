import { readFileSync, readdirSync, statSync } from "node:fs";
import { resolve, relative, sep } from "node:path";
import ts from "typescript";

const rootArg = process.argv.indexOf("--root");
const root = resolve(rootArg >= 0 ? process.argv[rootArg + 1] : "..");
const webRoot = resolve(root, "web");
const facts = [];

function posix(path) { return relative(root, path).split(sep).join("/"); }
function walk(path) {
  if (!statSync(path).isDirectory()) return [path];
  return readdirSync(path, { withFileTypes: true }).flatMap(entry => {
    if (["node_modules", "dist", ".next", ".open-next"].includes(entry.name)) return [];
    return walk(resolve(path, entry.name));
  });
}
function add(type, path, node, detail = {}) {
  const source = node.getSourceFile();
  const start = source.getLineAndCharacterOfPosition(node.getStart(source));
  const end = source.getLineAndCharacterOfPosition(node.getEnd());
  facts.push({
    id: `${type}:${path}:${start.line + 1}:${detail.name ?? detail.target ?? ""}`,
    type, path, line: start.line + 1, end_line: end.line + 1,
    extractor: "typescript-compiler-api", certainty: "EXACT", ...detail,
  });
}
function sqlDetail(sql) {
  const patterns = [
    ["SCHEMA_DDL", /^\s*CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+[`"[]?([A-Za-z_]\w*)/i],
    ["READ", /^\s*SELECT\b[\s\S]*?\bFROM\s+[`"[]?([A-Za-z_]\w*)/i],
    ["WRITE", /^\s*INSERT\s+INTO\s+[`"[]?([A-Za-z_]\w*)/i],
    ["WRITE", /^\s*UPDATE\s+[`"[]?([A-Za-z_]\w*)/i],
    ["DELETE", /^\s*DELETE\s+FROM\s+[`"[]?([A-Za-z_]\w*)/i],
  ];
  for (const [operation, pattern] of patterns) {
    const match = sql.match(pattern);
    if (match) return { operation, table: match[1].toLowerCase(), sql_literal: true };
  }
  return { operation: "UNRESOLVED", table: "UNRESOLVED", sql_literal: true };
}

for (const fullPath of walk(webRoot).filter(path => /\.(?:ts|tsx|mts|cts)$/.test(path))) {
  const path = posix(fullPath);
  const source = ts.createSourceFile(fullPath, readFileSync(fullPath, "utf8"), ts.ScriptTarget.Latest, true,
    fullPath.endsWith("x") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  if (/^web\/app\//.test(path) && /\/(?:page|route)\.(?:ts|tsx)$/.test(path)) {
    const route = path.replace(/^web\/app/, "").replace(/\/(?:page|route)\.(?:ts|tsx)$/, "") || "/";
    add(path.includes("/route.") ? "web_api_route" : "web_page_route", path, source, {
      route, classification: route.startsWith("/admin") ? "ADMIN" : "PUBLIC",
    });
  }
  function visit(node) {
    if (ts.isImportDeclaration(node) && ts.isStringLiteral(node.moduleSpecifier)) {
      add("web_import", path, node, { target: node.moduleSpecifier.text });
    }
    if (ts.isCallExpression(node) && node.arguments.length) {
      const expression = node.expression.getText(source);
      const first = node.arguments[0];
      if ((expression.endsWith(".prepare") || expression.endsWith(".exec")) && ts.isStringLiteralLike(first)) {
        add("d1_sql", path, node, sqlDetail(first.text));
      }
      if (/route|dispatch|pathname|method/i.test(expression) && ts.isStringLiteralLike(first) && first.text.startsWith("/")) {
        add("worker_dispatch", path, node, { route: first.text, handler: expression });
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(source);
}

facts.sort((a, b) => a.id.localeCompare(b.id));
process.stdout.write(JSON.stringify({ schema: "architecture-typescript-facts-v1", facts }));

