from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
MODEL_ROOT = ROOT / "formal" / "release-control"
MANIFEST = MODEL_ROOT / "shards.json"
TLA_TOOLS_VERSION = "v1.8.0"
TLA_TOOLS_URL = f"https://github.com/tlaplus/tlaplus/releases/download/{TLA_TOOLS_VERSION}/tla2tools.jar"
TLA_TOOLS_SHA256 = "eabd140a70f49eb9305a3bd3f3df944eddf87e5a90d329789085f8953a80533a"
PROGRESS = re.compile(r"(?P<generated>[\d,]+) states generated.*?(?P<distinct>[\d,]+) distinct states found.*?(?P<queued>[\d,]+) states left on queue")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ensure_tools(cache: Path) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    jar = cache / f"tla2tools-{TLA_TOOLS_VERSION}.jar"
    if jar.exists() and _sha256(jar) == TLA_TOOLS_SHA256:
        return jar
    temporary = jar.with_suffix(".tmp")
    temporary.unlink(missing_ok=True)
    with urlopen(TLA_TOOLS_URL, timeout=60) as response, temporary.open("wb") as output:
        shutil.copyfileobj(response, output)
    actual = _sha256(temporary)
    if actual != TLA_TOOLS_SHA256:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"TLA_TOOLS_DIGEST_MISMATCH: expected {TLA_TOOLS_SHA256}, got {actual}")
    temporary.replace(jar)
    return jar


def _load_shard(shard_id: str) -> dict[str, object]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    matches = [item for item in manifest["shards"] if item["id"] == shard_id]
    if len(matches) != 1:
        raise RuntimeError(f"UNKNOWN_TLA_SHARD: {shard_id}")
    return matches[0]


def _number(value: str) -> int:
    return int(value.replace(",", ""))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shard", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if shutil.which("java") is None:
        raise RuntimeError("JAVA_11_OR_NEWER_REQUIRED")
    shard = _load_shard(args.shard)
    jar = _ensure_tools((ROOT / ".local" / "tools").resolve())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    generated = distinct = 0
    max_queue: int | None = None
    started = time.monotonic()
    log_path = args.report.with_suffix(".log")
    with tempfile.TemporaryDirectory(prefix=f"xauusd-tlc-{args.shard}-") as model_dir:
        command = [
            "java", "-XX:+UseParallelGC", "-cp", str(jar), "tlc2.TLC",
            "-cleanup", "-noGenerateSpecTE", "-deadlock", "-workers", "auto",
            "-metadir", model_dir, "-config", str(shard["config"]), str(shard["module"]),
        ]
        process = subprocess.Popen(
            command, cwd=MODEL_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace"
        )
        assert process.stdout is not None
        with log_path.open("w", encoding="utf-8") as log:
            for line in process.stdout:
                print(line, end="", flush=True)
                log.write(line)
                match = PROGRESS.search(line)
                if match:
                    generated = max(generated, _number(match.group("generated")))
                    distinct = max(distinct, _number(match.group("distinct")))
                    queued = _number(match.group("queued"))
                    if queued > 0:
                        max_queue = max(max_queue or 0, queued)
        return_code = process.wait()
    report = {
        "shard": args.shard, "module": shard["module"], "config": shard["config"],
        "kind": shard["kind"], "elapsed_seconds": round(time.monotonic() - started, 3),
        "generated_states": generated, "distinct_states": distinct,
        "maximum_queue_depth": max_queue,
        "result": "PASS" if return_code == 0 else "FAIL",
        "properties": shard["properties"],
    }
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True), flush=True)
    return return_code


if __name__ == "__main__":
    sys.exit(main())
