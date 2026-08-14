const CLOUDFLARE_WORKERS_STUB = `
const env = new Proxy({}, {
  get(_target, key) {
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
  return nextResolve(specifier, context);
}
