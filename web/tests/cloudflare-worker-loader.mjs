const CLOUDFLARE_WORKERS_STUB = `
const env = new Proxy({}, {
  get(_target, key) {
    if (globalThis.__AURUM_TEST_WORKER_ENV) {
      return globalThis.__AURUM_TEST_WORKER_ENV[key];
    }
    throw new Error(\`Preview write touched Cloudflare env before rejection: \${String(key)}\`);
  },
});
export { env };
`;

export async function resolve(specifier, context, nextResolve) {
  if (specifier === "cloudflare:workers") {
    return {
      shortCircuit: true,
      url: `data:text/javascript,${encodeURIComponent(CLOUDFLARE_WORKERS_STUB)}`,
    };
  }
  try {
    return await nextResolve(specifier, context);
  } catch (error) {
    if (
      error?.code === "ERR_MODULE_NOT_FOUND"
      && (specifier.startsWith("./") || specifier.startsWith("../"))
      && !/\.[A-Za-z0-9]+$/.test(specifier)
    ) {
      return nextResolve(`${specifier}.ts`, context);
    }
    throw error;
  }
}
