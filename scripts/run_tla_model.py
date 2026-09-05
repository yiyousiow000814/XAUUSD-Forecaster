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
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MODEL_ROOT = ROOT / "formal" / "release-control"
MANIFEST = MODEL_ROOT / "shards.json"
TOOL_LOCK = ROOT / "formal" / "tools" / "tlc" / "tool-lock.json"
PROGRESS = re.compile(r"(?P<generated>[\d,]+) states generated.*?(?P<distinct>[\d,]+) distinct states found.*?(?P<queued>[\d,]+) states left on queue")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ToolError(RuntimeError):
    def __init__(self, state: str, detail: str):
        super().__init__(f"{state}: {detail}")
        self.state = state


def _load_tool_lock() -> dict:
    try:
        lock = json.loads(TOOL_LOCK.read_text(encoding="utf-8"))
        if (lock["schema_version"] != 1 or not re.fullmatch(r"[0-9a-f]{64}", lock["sha256"])
                or type(lock["size"]) is not int or not 0 < lock["size"] <= 16_000_000
                or not (ROOT / lock["artifact"]).resolve().is_relative_to(ROOT.resolve())):
            raise ValueError("invalid lock fields")
        return lock
    except FileNotFoundError as error:
        raise ToolError("TOOL_UNAVAILABLE", "repository tool lock missing") from error
    except (KeyError, TypeError, ValueError) as error:
        raise ToolError("TOOL_INTEGRITY_FAILED", "invalid tool lock") from error


def _verify_jar(path: Path, lock: dict) -> dict[str, str]:
    if not path.is_file():
        raise ToolError("TOOL_UNAVAILABLE", f"pinned JAR missing: {path}")
    if path.stat().st_size != lock["size"] or _sha256(path) != lock["sha256"]:
        raise ToolError("TOOL_INTEGRITY_FAILED", f"pinned JAR size/digest mismatch: {path}")
    try:
        with zipfile.ZipFile(path) as archive:
            if (sum(entry.file_size for entry in archive.infolist()) > 100_000_000
                    or archive.testzip() is not None or "tlc2/TLC.class" not in archive.namelist()):
                raise ValueError("invalid JAR structure")
            text = archive.read("META-INF/MANIFEST.MF").decode("utf-8")
            fields = dict(line.split(": ", 1) for line in text.splitlines() if ": " in line)
            return fields
    except (zipfile.BadZipFile, KeyError, UnicodeError, ValueError) as error:
        raise ToolError("TOOL_INTEGRITY_FAILED", "invalid JAR archive/manifest") from error


def _ensure_tools(cache: Path, lock: dict | None = None) -> Path:
    lock = lock if lock is not None else _load_tool_lock()
    source = ROOT / lock["artifact"]
    _verify_jar(source, lock)
    directory = cache / lock["sha256"]
    directory.mkdir(parents=True, exist_ok=True)
    jar = directory / "tla2tools.jar"
    if jar.exists():
        _verify_jar(jar, lock)  # Corruption is never silently replaced or trusted.
    else:
        with tempfile.NamedTemporaryFile(dir=directory, suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
        try:
            shutil.copyfile(source, temporary)
            _verify_jar(temporary, lock)
            temporary.replace(jar)
        finally:
            temporary.unlink(missing_ok=True)
    fields = _verify_jar(jar, lock)
    # Manifest Class-Path entries must not load unpinned adjacent dependencies.
    for dependency in fields.get("Class-Path", "").split():
        if (directory / dependency).exists():
            raise ToolError("TOOL_INTEGRITY_FAILED", "unexpected adjacent JAR dependency")
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
    parser.add_argument("--output", choices=("local", "ci"), required=True)
    args = parser.parse_args()
    shard = _load_shard(args.shard)
    safe_shard_id = str(shard["id"])
    output_root = ROOT / (".local/formal-results" if args.output == "local" else "formal-results")
    report_path = output_root / f"{safe_shard_id}.json"
    output_root.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    log_path = report_path.with_suffix(".log")
    report = {
        "shard": safe_shard_id, "module": shard["module"], "config": shard["config"],
        "kind": shard["kind"], "properties": shard["properties"],
        "generated_states": 0, "distinct_states": 0, "maximum_queue_depth": None,
        "result": "TOOL_UNAVAILABLE",
    }
    try:
        return_code = _execute(shard, report, log_path)
    except ToolError as error:
        report.update(result=error.state, diagnostic=str(error))
        return_code = 2
    except (OSError, subprocess.SubprocessError) as error:
        report.update(result="TOOL_UNAVAILABLE", diagnostic=str(error))
        return_code = 2
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True), flush=True)
    return return_code


def _execute(shard: dict, report: dict, log_path: Path) -> int:
    lock = _load_tool_lock()
    report["tool_identity"] = lock
    jar = _ensure_tools((ROOT / ".local" / "tools").resolve(), lock)
    if shutil.which("java") is None:
        raise ToolError("TOOL_UNAVAILABLE", "JAVA_11_OR_NEWER_REQUIRED")
    java = subprocess.run(["java", "-version"], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=10, check=True)
    report["java_version"] = (java.stdout + java.stderr).strip()
    report["jar_manifest"] = _verify_jar(jar, lock)
    report["source_sha"] = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", timeout=10, check=True,
    ).stdout.strip()
    report["source_dirty"] = bool(subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=normal"], cwd=ROOT,
        capture_output=True, text=True, encoding="utf-8", timeout=10, check=True,
    ).stdout.strip())
    # Include local INSTANCE/EXTENDS dependencies, not just the entry module.
    report["model_files_sha256"] = {
        path.name: _sha256(path) for path in sorted(MODEL_ROOT.glob("*.tla"))
    }
    report["config_sha256"] = _sha256(MODEL_ROOT / str(shard["config"]))
    generated = distinct = 0
    max_queue: int | None = None
    with tempfile.TemporaryDirectory(prefix=f"xauusd-tlc-{shard['id']}-") as model_dir:
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
                if line.startswith("TLC2 Version"):
                    report["tlc_version"] = line.strip()
                match = PROGRESS.search(line)
                if match:
                    generated = max(generated, _number(match.group("generated")))
                    distinct = max(distinct, _number(match.group("distinct")))
                    queued = _number(match.group("queued"))
                    if queued > 0:
                        max_queue = max(max_queue or 0, queued)
        return_code = process.wait()
    report.update({
        "generated_states": generated, "distinct_states": distinct,
        "maximum_queue_depth": max_queue,
        "result": "PASS" if return_code == 0 else "MODEL_CHECK_FAILED",
    })
    return return_code


if __name__ == "__main__":
    sys.exit(main())
