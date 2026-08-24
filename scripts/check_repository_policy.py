from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


AUTOMATION_ROOTS = (
    Path(".github/workflows"),
    Path(".github/actions"),
    Path("ctrader"),
    Path("scripts"),
    Path("web"),
    Path("xauusd_forecaster"),
)
AUTOMATION_SUFFIXES = {
    ".cs",
    ".js",
    ".json",
    ".mjs",
    ".ps1",
    ".py",
    ".sh",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
}
IGNORED_PARTS = {".next", ".open-next", "node_modules", "__pycache__"}
POLICY_IMPLEMENTATION = Path("scripts/check_repository_policy.py")
CLOUDFLARE_BUILD_CONTRACT = Path("web/cloudflare-build-contract.json")
EXPECTED_CLOUDFLARE_BUILD_CONTRACT = {
    "schema_version": "cloudflare-production-build-v1",
    "source": {
        "provider": "github",
        "repository": "yiyousiow000814/XAUUSD-Forecaster",
        "production_branch": "main",
        "root_directory": "/web",
        "path_includes": ["*"],
        "path_excludes": [],
    },
    "commands": {
        "build": "npm ci && npm test",
        "deploy": (
            'npx wrangler versions upload --message '
            '"release:$WORKERS_CI_COMMIT_SHA branch:$WORKERS_CI_BRANCH '
            'artifact_kind:PRODUCTION_CANDIDATE"'
        ),
    },
    "output": {
        "artifact_kind": "PRODUCTION_CANDIDATE",
        "immutable_version_only": True,
        "changes_stable_traffic": False,
    },
    "non_production_builds_enabled": False,
}

YAML_ENVIRONMENT_KEY = re.compile(
    r"(?:^|[{,])\s*(?:environment|'environment'|\"environment\")\s*:",
    re.IGNORECASE,
)
YAML_DEPLOYMENTS_WRITE = re.compile(
    r"(?:^|[{,])\s*(?:deployments|'deployments'|\"deployments\")\s*:\s*"
    r"(?:write|'write'|\"write\")(?:\s*[,}]|\s*$)",
    re.IGNORECASE,
)
YAML_WRITE_ALL = re.compile(
    r"(?:^|[{,])\s*(?:permissions|'permissions'|\"permissions\")\s*:\s*"
    r"(?:write-all|'write-all'|\"write-all\")(?:\s*[,}]|\s*$)",
    re.IGNORECASE,
)
YAML_ACTION_USES = re.compile(
    r"^\s*(?:-\s*)?uses:\s*['\"]?(?P<target>[^'\"#\s]+)",
    re.IGNORECASE,
)
IMMUTABLE_ACTION_REF = re.compile(r"^[^@\s]+@[0-9a-f]{40}$", re.IGNORECASE)
GITHUB_DEPLOYMENT_API_URL = re.compile(
    r"https?://api\.github\.com/repos/[^/\s'\"`]+/[^/\s'\"`]+/"
    r"(?:deployments|environments)(?:[/\s'\"`]|$)",
    re.IGNORECASE,
)
GITHUB_CLI_DEPLOYMENT_API = re.compile(
    r"\bgh(?:\.exe)?\b[^\r\n]{0,80}\bapi\b[^\r\n]{0,200}"
    r"\brepos/[^/\s'\"`]+/[^/\s'\"`]+/(?:deployments|environments)"
    r"(?:[/\s'\"`]|$)",
    re.IGNORECASE,
)
GITHUB_DEPLOYMENT_CLIENT = re.compile(
    r"\b(?:github|octokit)\.rest\.repos\."
    r"(?:createDeployment|createDeploymentStatus|createOrUpdateEnvironment)\s*\(",
)
GITHUB_DEPLOYMENT_ACTION = re.compile(
    r"\b"
    r"(?:chrnorm/deployment-action|bobheadxi/deployments|"
    r"unacast/actions-github-deployment-status)@",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PolicyViolation:
    path: Path
    line: int
    boundary: str

    def render(self) -> str:
        return f"{self.path.as_posix()}:{self.line}: {self.boundary}"


def _strip_line_comments(line: str, *, hash_comments: bool, slash_comments: bool) -> str:
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(line):
        char = line[index]
        if escaped:
            escaped = False
        elif char == "\\" and quote is not None:
            escaped = True
        elif quote is not None:
            if char == quote:
                quote = None
        elif char in {"'", '"', "`"}:
            quote = char
        elif hash_comments and char == "#":
            return line[:index]
        elif slash_comments and line[index : index + 2] == "//":
            return line[:index]
        index += 1
    return line


def _uncommented_lines(path: Path, text: str) -> Iterable[tuple[int, str]]:
    suffix = path.suffix.lower()
    hash_comments = suffix in {".py", ".ps1", ".sh", ".yaml", ".yml"}
    slash_comments = suffix in {".cs", ".js", ".mjs", ".ts", ".tsx"}
    in_block_comment = False

    for number, original in enumerate(text.splitlines(), start=1):
        line = original
        if slash_comments:
            cleaned: list[str] = []
            index = 0
            while index < len(line):
                if in_block_comment:
                    end = line.find("*/", index)
                    if end == -1:
                        index = len(line)
                        continue
                    in_block_comment = False
                    index = end + 2
                    continue
                start = line.find("/*", index)
                if start == -1:
                    cleaned.append(line[index:])
                    break
                cleaned.append(line[index:start])
                index = start + 2
                in_block_comment = True
            line = "".join(cleaned)
        yield number, _strip_line_comments(
            line,
            hash_comments=hash_comments,
            slash_comments=slash_comments,
        )


def _active_yaml_lines(path: Path, text: str) -> Iterable[tuple[int, str]]:
    block_parent_indent: int | None = None
    for number, line in _uncommented_lines(path, text):
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        if block_parent_indent is not None:
            if indent > block_parent_indent:
                continue
            block_parent_indent = None
        yield number, line
        if re.search(r":\s*[|>][+-]?\s*$", line):
            block_parent_indent = indent


def _automation_files(root: Path) -> Iterable[tuple[Path, Path]]:
    for relative_root in AUTOMATION_ROOTS:
        directory = root / relative_root
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*")):
            relative = path.relative_to(root)
            if (
                path.is_file()
                and not path.is_symlink()
                and path.suffix.lower() in AUTOMATION_SUFFIXES
                and relative != POLICY_IMPLEMENTATION
                and not IGNORED_PARTS.intersection(relative.parts)
            ):
                yield path, relative


def check_repository(root: Path) -> list[PolicyViolation]:
    root = root.resolve()
    violations: list[PolicyViolation] = []

    build_contract_path = root / CLOUDFLARE_BUILD_CONTRACT
    try:
        build_contract = json.loads(build_contract_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        violations.append(PolicyViolation(
            CLOUDFLARE_BUILD_CONTRACT,
            1,
            "exact-main immutable Cloudflare production build contract is required",
        ))
    else:
        if build_contract != EXPECTED_CLOUDFLARE_BUILD_CONTRACT:
            violations.append(PolicyViolation(
                CLOUDFLARE_BUILD_CONTRACT,
                1,
                "Cloudflare production build contract drifted from exact-main immutable upload",
            ))

    package_path = root / "web/package.json"
    if package_path.exists():
        try:
            package = json.loads(package_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            package = {}
        for command in package.get("scripts", {}).values():
            if re.search(r"\bwrangler(?:\.cmd)?\s+deploy\b", str(command), re.IGNORECASE):
                violations.append(PolicyViolation(
                    Path("web/package.json"),
                    1,
                    "direct production wrangler deploy script is forbidden",
                ))
                break

    for path, relative in _automation_files(root):
        text = path.read_text(encoding="utf-8", errors="replace")
        if relative.parts[:2] == (".github", "workflows"):
            for number, line in _active_yaml_lines(path, text):
                action = YAML_ACTION_USES.match(line)
                if action:
                    target = action.group("target")
                    if (
                        not target.startswith("./")
                        and not target.startswith("docker://")
                        and not IMMUTABLE_ACTION_REF.fullmatch(target)
                    ):
                        violations.append(
                            PolicyViolation(
                                relative,
                                number,
                                "GitHub Action must be pinned to a full commit SHA",
                            )
                        )
                if YAML_ENVIRONMENT_KEY.search(line):
                    violations.append(
                        PolicyViolation(relative, number, "GitHub Actions environment is forbidden")
                    )
                if YAML_DEPLOYMENTS_WRITE.search(line):
                    violations.append(
                        PolicyViolation(relative, number, "deployments: write is forbidden")
                    )
                if YAML_WRITE_ALL.search(line):
                    violations.append(
                        PolicyViolation(relative, number, "permissions: write-all is forbidden")
                    )

        for number, line in _uncommented_lines(path, text):
            if (
                GITHUB_DEPLOYMENT_API_URL.search(line)
                or GITHUB_CLI_DEPLOYMENT_API.search(line)
            ):
                violations.append(
                    PolicyViolation(relative, number, "GitHub Deployments/Environments API is forbidden")
                )
            if GITHUB_DEPLOYMENT_CLIENT.search(line):
                violations.append(
                    PolicyViolation(relative, number, "GitHub deployment client call is forbidden")
                )
            if GITHUB_DEPLOYMENT_ACTION.search(line):
                violations.append(
                    PolicyViolation(relative, number, "GitHub deployment action is forbidden")
                )

    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description="Check repository architecture policy")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    violations = check_repository(args.root)
    if violations:
        print("Cloudflare-only hosting policy violations:")
        for violation in violations:
            print(f"::error file={violation.path.as_posix()},line={violation.line}::{violation.boundary}")
            print(violation.render())
        return 1
    print("Repository policy passed: hosting remains Cloudflare-only.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
