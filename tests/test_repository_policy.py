from __future__ import annotations

from pathlib import Path
import json

import pytest

from scripts.check_repository_policy import (
    EXPECTED_CLOUDFLARE_BUILD_CONTRACT,
    check_repository,
)


VALID_BUILD_CONTRACT = json.dumps(EXPECTED_CLOUDFLARE_BUILD_CONTRACT)


@pytest.fixture(autouse=True)
def cloudflare_build_contract(tmp_path: Path) -> None:
    write(tmp_path, "web/cloudflare-build-contract.json", VALID_BUILD_CONTRACT)


def write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def boundaries(root: Path) -> list[str]:
    return [violation.boundary for violation in check_repository(root)]


def test_allows_normal_workflows_cloudflare_and_non_github_routes(tmp_path: Path) -> None:
    write(
        tmp_path,
        ".github/workflows/preview.yml",
        """name: Cloudflare Preview
on: pull_request
permissions:
  contents: read
jobs:
  preview:
    runs-on: ubuntu-latest
    steps:
      - run: npx wrangler deploy
      - run: |
          echo "environment: documentation example"
          echo "deployments: write"
""",
    )
    write(
        tmp_path,
        "web/app/deployments/route.ts",
        """// gh api repos/example/project/deployments
export const route = "/deployments";
export const unrelated = "https://example.com/repos/acme/project/deployments";
export const cloudflare = "https://preview.example.workers.dev";
""",
    )
    write(tmp_path, "docs/example.md", "environment: production\ndeployments: write\n")

    assert check_repository(tmp_path) == []


@pytest.mark.parametrize(
    ("workflow", "expected"),
    [
        (
            """jobs:
  publish:
    environment: production
""",
            "GitHub Actions environment is forbidden",
        ),
        (
            """permissions:
  deployments: write
jobs: {}
""",
            "deployments: write is forbidden",
        ),
        (
            """permissions:
  contents: read
jobs:
  publish:
    permissions: {contents: read, deployments: write}
""",
            "deployments: write is forbidden",
        ),
        (
            """permissions: write-all
jobs: {}
""",
            "permissions: write-all is forbidden",
        ),
    ],
)
def test_rejects_forbidden_workflow_architecture(
    tmp_path: Path,
    workflow: str,
    expected: str,
) -> None:
    write(tmp_path, ".github/workflows/publish.yml", workflow)

    assert expected in boundaries(tmp_path)


def test_rejects_mutable_action_tags_and_accepts_immutable_refs(tmp_path: Path) -> None:
    write(
        tmp_path,
        ".github/workflows/unsafe.yml",
        """jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
""",
    )
    assert "GitHub Action must be pinned to a full commit SHA" in boundaries(tmp_path)

    write(
        tmp_path,
        ".github/workflows/unsafe.yml",
        """jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4
      - uses: ./.github/actions/local
""",
    )
    assert check_repository(tmp_path) == []


@pytest.mark.parametrize(
    "source",
    [
        'subprocess.run(["gh", "api", "repos/acme/project/deployments"])',
        'requests.post("https://api.github.com/repos/acme/project/environments/prod")',
        "github.rest.repos.createDeployment({owner, repo})",
        "uses: chrnorm/deployment-action@v2",
    ],
)
def test_rejects_github_deployment_automation(tmp_path: Path, source: str) -> None:
    write(tmp_path, "scripts/publish.py", source)

    assert check_repository(tmp_path)


def test_current_repository_satisfies_hosting_policy() -> None:
    root = Path(__file__).resolve().parents[1]

    assert check_repository(root) == []


def test_rejects_missing_or_mutable_production_build_contract(tmp_path: Path) -> None:
    contract = tmp_path / "web" / "cloudflare-build-contract.json"
    contract.unlink()
    assert "exact-main immutable Cloudflare production build contract is required" in boundaries(tmp_path)

    write(
        tmp_path,
        "web/cloudflare-build-contract.json",
        VALID_BUILD_CONTRACT.replace("versions upload", "deploy"),
    )
    assert (
        "Cloudflare production build contract drifted from exact-main immutable upload"
        in boundaries(tmp_path)
    )


def test_rejects_direct_production_deploy_package_script(tmp_path: Path) -> None:
    write(
        tmp_path,
        "web/package.json",
        '{"scripts":{"cf:deploy":"npm test && wrangler deploy"}}',
    )
    assert "direct production wrangler deploy script is forbidden" in boundaries(tmp_path)
