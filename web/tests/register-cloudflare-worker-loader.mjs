import { register } from "node:module";

register("./cloudflare-worker-loader.mjs", import.meta.url);
