from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
MODEL_ROOT = ROOT / "formal" / "release-control"
TLA_TOOLS_VERSION = "v1.8.0"
TLA_TOOLS_URL = (
    "https://github.com/tlaplus/tlaplus/releases/download/"
    f"{TLA_TOOLS_VERSION}/tla2tools.jar"
)
TLA_TOOLS_SHA256 = "eabd140a70f49eb9305a3bd3f3df944eddf87e5a90d329789085f8953a80533a"
CONFIGS = ("ReleaseControlSafety.cfg", "ReleaseControlLiveness.cfg")


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
        raise RuntimeError(
            f"TLA_TOOLS_DIGEST_MISMATCH: expected {TLA_TOOLS_SHA256}, got {actual}"
        )
    temporary.replace(jar)
    return jar


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the pinned Release Control TLC models")
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / ".local" / "tools",
        help="download cache for the verified tla2tools.jar",
    )
    parser.add_argument(
        "--config",
        action="append",
        choices=CONFIGS,
        help="run only the selected config; may be repeated",
    )
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument(
        "--java",
        type=Path,
        help="explicit Java executable; defaults to java on PATH",
    )
    args = parser.parse_args()

    java = str(args.java.resolve()) if args.java else shutil.which("java")
    if java is None:
        raise RuntimeError("JAVA_11_OR_NEWER_REQUIRED")
    if args.java and not Path(java).is_file():
        raise RuntimeError(f"JAVA_EXECUTABLE_NOT_FOUND: {java}")
    jar = _ensure_tools(args.cache.resolve())
    configs = tuple(args.config or CONFIGS)
    for config in configs:
        with tempfile.TemporaryDirectory(prefix="xauusd-tlc-") as model_dir:
            command = [
                java,
                "-XX:+UseParallelGC",
                "-cp",
                str(jar),
                "tlc2.TLC",
                "-cleanup",
                "-noGenerateSpecTE",
                "-deadlock",
                "-workers",
                str(args.workers),
                "-metadir",
                model_dir,
                "-config",
                config,
                "ReleaseControl.tla",
            ]
            print(f"TLC {config}", flush=True)
            result = subprocess.run(command, cwd=MODEL_ROOT, check=False)
            if result.returncode:
                return result.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
