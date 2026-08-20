import { cp, mkdir, readdir } from "node:fs/promises";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const source = fileURLToPath(new URL("../dist/server/prerendered-routes/", import.meta.url));
const destination = fileURLToPath(new URL("../dist/client/", import.meta.url));

await mkdir(destination, { recursive: true });
await cp(source, destination, { recursive: true, force: true });

async function listHtml(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) files.push(...await listHtml(path));
    else if (entry.name.endsWith(".html")) files.push(path);
  }
  return files;
}

const published = await listHtml(source);
if (!published.some(path => relative(source, path).replaceAll("\\", "/") === "index.html")) {
  throw new Error("vinext did not prerender the public root shell");
}

console.log(`Published ${published.length} prerendered HTML assets.`);
