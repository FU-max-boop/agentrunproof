from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "publish.yml"


def test_v012_uses_the_upstream_comparison_release_profile() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "default: v0.1.2" in workflow
    assert "v0.1.0)" in workflow
    assert "v0.1.1)" in workflow
    assert "v0.1.2)" in workflow
    assert "evidence_directory=evidence/upstream-comparison/v1" in workflow
    assert "evidence_checker=check-upstream-bundle" in workflow
    assert "evidence_kind=upstream-comparison" in workflow


def test_v012_release_assets_are_derived_from_every_bundle_member() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert 'bundle["harness"]["wheel"]["path"]' in workflow
    assert 'target["source_wheel"]["path"]' in workflow
    assert 'run["certificate"]["path"] for run in bundle["runs"]' in workflow
    assert "actual != expected" in workflow
    assert "local.keys() == remote.keys()" in workflow
    assert 'len(remote) == len(release["assets"])' in workflow
    assert 'remote[name]["size"] == len(data)' in workflow
    assert 'remote[name]["digest"] == "sha256:" + hashlib.sha256(data).hexdigest()' in workflow


def test_v012_evidence_commit_is_minimal_and_is_the_tag_target() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    tag_policy = workflow.rsplit('case "$RELEASE_TAG" in', 1)[1]
    v012_policy = tag_policy.split("v0.1.2)", 1)[1].split(";;", 1)[0]

    assert 'git merge-base --is-ancestor "$source_commit" "$evidence_parent"' in workflow
    assert 'git diff --name-only "$evidence_parent..$evidence_commit"' in workflow
    assert 'git diff --exit-code "$evidence_commit" HEAD -- "$EVIDENCE_DIRECTORY"' in workflow
    assert 'test "$(git rev-parse HEAD)" = "$evidence_commit"' in v012_policy
    assert 'bundle_path.parent / run["certificate"]["path"]' in workflow


def test_bundle_checker_runs_from_the_installed_release_wheel() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    download_step = workflow.split("      - name: Download and bind the final GitHub Release\n", 1)[
        1
    ].split("      - name: Rebuild the tagged source and check the evidence bundle\n", 1)[0]
    rebuild_step = workflow.split(
        "      - name: Rebuild the tagged source and check the evidence bundle\n", 1
    )[1].split("      - name: Redownload and stage the final immutable bytes\n", 1)[0]

    assert "PYTHONPATH=src python -m agentrunproof" not in download_step
    assert '"$RUNNER_TEMP/release-smoke/bin/agentrunproof"' in rebuild_step
    assert '"$EVIDENCE_CHECKER" release-assets/bundle.json' in rebuild_step
