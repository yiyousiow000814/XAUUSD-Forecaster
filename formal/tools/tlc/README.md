# Pinned TLC developer tool

`tool-lock.json` is the single executable artifact identity authority. The JAR
is versioned with the repository, not downloaded by CI or the model runner.
Cold and warm runs verify its full size, SHA-256 and ZIP structure before Java
starts. A digest-addressed local copy is only a cache; corrupt caches fail,
and missing sources never fall back to a rolling release or another tool.

The upstream `v1.8.0` tag is a rolling, mutable prerelease. The official README
states that every master commit is rebuilt and uploaded there. Consequently
the former pinned digest could not be retrieved from that URL. Bounded checks
of eight known worktree tool caches found no copy matching the former pin.
This revision adopts only the specifically reviewed asset recorded in the lock.
Its asset ID alone does not guarantee future upstream availability.

The official release/asset metadata, downloaded size and full digest agreed.
All 2,223 ZIP entries passed CRC verification. Manifest source/build fields are
self-reported artifact metadata, **not** an independently verified build
attestation. The digest attestation API returned 404; independent source-build
proof remains UNKNOWN. The previous cache copies were not accepted or executed.
The manifest records a changed build; no previous formal PASS is reused.

The included MIT notice is the upstream LICENSE blob
`6a33a764282d054c3f2e9787d0763079130ee6a2`. Additional license and attribution
texts remain inside the unmodified JAR: `License.txt`, `META-INF/LICENSE.md`,
`CommonsMath-LICENSE.txt`, `CommonsMath-NOTICE.txt`, `jline-LICENSE.txt`, and
the corresponding Apache Commons Math package notices. No runtime dependency
or production storage service is introduced.

## Change and verification boundary

Runner -> reviewed lock -> repository JAR -> digest-addressed local cache ->
existing JDK -> unchanged selected modules/configurations. Missing inputs are
TOOL_UNAVAILABLE; altered/invalid bytes are TOOL_INTEGRITY_FAILED; unsuccessful
TLC execution is MODEL_CHECK_FAILED. Only an actual successful model check is
PASS. Cold/hot, corrupt/HTML/truncated, unavailable-source and before-Java
failure contracts run offline. Actual model results include JDK, TLC build,
tool digest, source SHA/dirty status and module/config/dependency hashes.

Local Windows and Linux CI use this same resolver and bundled artifact. Tool
changes select every required shard. Runtime model properties, the required
aggregator, and the five-minute CI budget are unchanged. Source WIP is never
represented as exact committed-source proof. Rollback of this development tool
requires another explicitly reviewed byte identity, never an automatic fallback.
