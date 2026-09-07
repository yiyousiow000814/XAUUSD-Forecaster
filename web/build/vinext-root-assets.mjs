import { posix } from "node:path";
import ts from "typescript";

const rootAssets = new Set([
  "./vinext-client-assets.js",
  "./__vinext_cacheability_manifest.js",
]);

// Vinext emits these modules at the RSC output root, including a late client
// asset update. Resolve imports from each chunk; never copy a stale snapshot.
export function resolveVinextRootAssetImports(code, fileName) {
  if (![...rootAssets].some(asset => code.includes(asset))) return null;
  const normalized = typeof fileName === "string" ? fileName.replaceAll("\\", "/") : "";
  if (!normalized || posix.isAbsolute(normalized) || /^[A-Za-z]:/.test(normalized)
      || normalized.split("/").some(part => !part || part === "." || part === "..")) {
    throw new Error("VINEXT_PRERENDER_CHUNK_PATH_INVALID");
  }
  const source = ts.createSourceFile(normalized, code, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
  if (source.parseDiagnostics.length) throw new Error("VINEXT_PRERENDER_IMPORT_PARSE_FAILED");
  const replacements = [];
  const inspect = node => {
    const specifier = ts.isImportDeclaration(node) || ts.isExportDeclaration(node)
      ? node.moduleSpecifier
      : ts.isCallExpression(node) && node.expression.kind === ts.SyntaxKind.ImportKeyword
        ? node.arguments[0] : undefined;
    if (specifier && ts.isStringLiteral(specifier) && rootAssets.has(specifier.text)) {
      let relative = posix.relative(posix.dirname(normalized), specifier.text.slice(2));
      if (!relative.startsWith(".")) relative = `./${relative}`;
      if (relative !== specifier.text) {
        replacements.push({ start: specifier.getStart(source), end: specifier.end, text: JSON.stringify(relative) });
      }
    }
    ts.forEachChild(node, inspect);
  };
  inspect(source);
  if (!replacements.length) return null;
  for (const replacement of replacements.sort((left, right) => right.start - left.start)) {
    code = code.slice(0, replacement.start) + replacement.text + code.slice(replacement.end);
  }
  return { code, map: null };
}

/** @returns {import("vite").Plugin} */
export function vinextRootAssets() {
  return {
    name: "aurum-vinext-lazy-entry-prerender",
    apply: "build",
    enforce: "post",
    renderChunk(code, chunk) {
      if (this.environment.name !== "rsc") return null;
      return resolveVinextRootAssetImports(code, chunk.fileName);
    },
  };
}
