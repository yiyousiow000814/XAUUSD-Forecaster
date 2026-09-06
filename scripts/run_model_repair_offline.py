"""Run the explicit frozen research experiment; no production entrypoints."""
import argparse
import json
from pathlib import Path
import sys
import hashlib
import subprocess
import platform
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from xauusd_forecaster.offline_model_repair import experiment


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--panel", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for source in (args.panel, args.plan):
        if source.resolve().is_relative_to(args.output.resolve()):
            parser.error("output must not own any input")
    result = experiment(json.loads(args.panel.read_text(encoding="utf-8")),
                        json.loads(args.plan.read_text(encoding="utf-8")), args.output)
    root = Path(__file__).resolve().parents[1]
    def git(*command):
        return subprocess.check_output(["git", *command], cwd=root, text=True, encoding="utf-8",
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).strip()
    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()
    files = ["scripts/run_model_repair_offline.py", "scripts/build_model_repair_panel.py", "scripts/report_model_repair_offline.py",
             "xauusd_forecaster/offline_model_repair.py", "xauusd_forecaster/ridge.py",
             "xauusd_forecaster/training.py", "xauusd_forecaster/execution_costs.py", "xauusd_forecaster/forward_ledger.py"]
    evidence = {"source_git_sha": git("rev-parse", "HEAD"), "worktree_clean": not bool(git("status", "--porcelain")),
                "source_files_sha256": {p: digest(root/p) for p in files},
                "panel_path": str(args.panel.resolve()), "panel_sha256": digest(args.panel),
                "plan_sha256": digest(args.plan), "python": sys.version, "numpy": np.__version__,
                "platform": platform.platform(), "command": sys.argv,
                "results_sha256": digest(args.output/"results.json"), "production_mutation": 0}
    (args.output/"execution.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps({"status": result["status"], "opportunities": result["opportunity_count"]}))


if __name__ == "__main__":
    main()
