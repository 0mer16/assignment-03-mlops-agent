"""DVC data-management tools.

DVC has no stable Python API for the handful of things the agent needs, so these
tools shell out. Every command is checked against an allowlist first: the agent
can read provenance but cannot push, remove or garbage-collect anything.

The dataset "version" reported here is the pair that actually identifies it: the
content hash recorded in the .dvc file, plus the Git commit that recorded it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from agent.config import get_settings
from agent.errors import PermissionDenied, ToolUnavailable
from agent.registry import ToolRegistry
from agent.schemas import (
    DatasetCompareInput,
    DatasetPathInput,
    DatasetReproduceInput,
    EmptyInput,
    ToolResult,
    ToolSpec,
    ToolStatus,
)

MODULE = "dvc"

# Read-only subcommands only. Anything that writes to a remote or deletes cache
# entries is refused before the process starts.
GIT_ALLOWED = {"log", "show", "tag", "rev-parse", "describe", "ls-files", "status", "diff", "cat-file"}
DVC_ALLOWED = {"status", "data", "list", "dag", "remote", "version", "diff", "doctor"}

_COMMAND_TIMEOUT = 25


def _resolve(executable: str) -> str | None:
    """Find a command on PATH, or alongside the interpreter running the agent.

    Without the second lookup the agent fails to find dvc whenever it runs from
    a virtual environment whose Scripts directory is not on PATH.
    """
    found = shutil.which(executable)
    if found:
        return found
    bindir = Path(sys.executable).parent
    for candidate in (bindir / executable, bindir / f"{executable}.exe"):
        if candidate.exists():
            return str(candidate)
    return None


def _run(executable: str, args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run an allowlisted git or dvc command and return the completed process."""
    allowed = GIT_ALLOWED if executable == "git" else DVC_ALLOWED
    if not args or args[0] not in allowed:
        raise PermissionDenied(
            f"{executable} subcommand {args[:1]} is not on the read-only allowlist {sorted(allowed)}"
        )
    binary = _resolve(executable)
    if binary is None:
        raise ToolUnavailable(f"{executable} is not installed or not on PATH")

    cwd = cwd or get_settings().repo_root
    try:
        return subprocess.run(  # noqa: S603 - fixed executable, allowlisted subcommand, no shell
            [binary, *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_COMMAND_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolUnavailable(f"{executable} {args[0]} timed out after {_COMMAND_TIMEOUT}s") from exc
    except OSError as exc:
        raise ToolUnavailable(f"could not run {executable}: {exc}") from exc


def _source(executable: str, args: list[str]) -> str:
    return f"{executable} {' '.join(args)}"


def _read_dvc_pointer(dvc_file: Path) -> dict[str, Any]:
    """Parse a .dvc pointer file into its recorded outputs."""
    content = yaml.safe_load(dvc_file.read_text(encoding="utf-8")) or {}
    outs = content.get("outs") or []
    return {
        "dvc_file": dvc_file.name,
        "outs": [
            {
                "path": out.get("path"),
                "hash": out.get("md5") or out.get("hash_value") or out.get("checksum"),
                "size_bytes": out.get("size"),
                "files": out.get("nfiles"),
            }
            for out in outs
        ],
    }


def list_tracked_datasets(_: EmptyInput) -> ToolResult:
    """Find every dataset DVC tracks in the repository."""
    root = get_settings().repo_root
    pointers = sorted(p for p in root.rglob("*.dvc") if ".dvc/" not in p.as_posix())

    datasets = []
    for pointer in pointers:
        info = _read_dvc_pointer(pointer)
        tracked_path = pointer.with_suffix("").relative_to(root).as_posix()
        datasets.append({"tracked_path": tracked_path, **info})

    # Pipeline outputs live in dvc.yaml rather than in a .dvc pointer file.
    pipeline_outputs: list[str] = []
    dvc_yaml = root / "dvc.yaml"
    if dvc_yaml.exists():
        spec = yaml.safe_load(dvc_yaml.read_text(encoding="utf-8")) or {}
        for stage_name, stage in (spec.get("stages") or {}).items():
            for out in stage.get("outs") or []:
                name = out if isinstance(out, str) else next(iter(out))
                pipeline_outputs.append(f"{stage_name}:{name}")

    if not datasets and not pipeline_outputs:
        return ToolResult(
            tool="dvc_list_tracked_datasets",
            status=ToolStatus.UNAVAILABLE,
            error="no DVC-tracked datasets found; has 'dvc add' been run in this repository?",
            source=str(root),
        )

    return ToolResult(
        tool="dvc_list_tracked_datasets",
        status=ToolStatus.OK,
        data={"datasets": datasets, "pipeline_outputs": pipeline_outputs, "count": len(datasets)},
        source=f"{root}/**/*.dvc",
    )


def dataset_version(payload: DatasetPathInput) -> ToolResult:
    """Report the content hash and the Git commit that recorded a dataset."""
    root = get_settings().repo_root
    pointer = root / f"{payload.path}.dvc"
    if not pointer.exists():
        return ToolResult(
            tool="dvc_dataset_version",
            status=ToolStatus.ERROR,
            error=f"{pointer.relative_to(root).as_posix()} does not exist; the path is not tracked by DVC",
            source=str(pointer),
        )

    info = _read_dvc_pointer(pointer)
    rel_pointer = pointer.relative_to(root).as_posix()

    log = _run("git", ["log", "-1", "--format=%H%x1f%h%x1f%aI%x1f%s", "--", rel_pointer])
    commit: dict[str, Any] = {}
    if log.returncode == 0 and log.stdout.strip():
        full, short, date, subject = log.stdout.strip().split("\x1f")
        commit = {"sha": full, "short_sha": short, "committed_at": date, "subject": subject}
        tags = _run("git", ["tag", "--points-at", full])
        commit["tags"] = [t for t in tags.stdout.split() if t]

    return ToolResult(
        tool="dvc_dataset_version",
        status=ToolStatus.OK,
        data={"tracked_path": payload.path, **info, "git_commit": commit},
        source=f"{rel_pointer} + git log",
    )


def dataset_status(_: EmptyInput) -> ToolResult:
    """Check whether tracked data differs from what DVC recorded."""
    result = _run("dvc", ["status", "--json"])
    if result.returncode != 0 and not result.stdout.strip():
        raise ToolUnavailable(f"dvc status failed: {result.stderr.strip() or 'no output'}")

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        payload = {"raw": result.stdout.strip()}

    changed = sorted(payload) if isinstance(payload, dict) else []
    return ToolResult(
        tool="dvc_dataset_status",
        status=ToolStatus.OK,
        data={
            "up_to_date": not changed,
            "changed_entries": changed,
            "detail": payload,
        },
        source=_source("dvc", ["status", "--json"]),
    )


def compare_versions(payload: DatasetCompareInput) -> ToolResult:
    """Compare what DVC tracked at two Git revisions."""
    result = _run("dvc", ["diff", payload.rev_a, payload.rev_b, "--json"])
    if result.returncode != 0 and not result.stdout.strip():
        raise ToolUnavailable(f"dvc diff failed: {result.stderr.strip() or 'no output'}")

    try:
        diff = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        diff = {"raw": result.stdout.strip()}

    counts = {key: len(value) for key, value in diff.items() if isinstance(value, list)}
    return ToolResult(
        tool="dvc_compare_versions",
        status=ToolStatus.OK,
        data={
            "rev_a": payload.rev_a,
            "rev_b": payload.rev_b,
            "changes": diff,
            "summary": counts,
            "identical": not any(counts.values()),
        },
        source=_source("dvc", ["diff", payload.rev_a, payload.rev_b, "--json"]),
    )


def reproduce_instructions(payload: DatasetReproduceInput) -> ToolResult:
    """Explain exactly how to restore a previous dataset version."""
    rel_pointer = f"{payload.path}.dvc"
    recorded_hash = None

    show = _run("git", ["show", f"{payload.rev}:{rel_pointer}"])
    if show.returncode == 0:
        try:
            outs = (yaml.safe_load(show.stdout) or {}).get("outs") or []
            recorded_hash = outs[0].get("md5") if outs else None
        except yaml.YAMLError:
            recorded_hash = None
    else:
        return ToolResult(
            tool="dvc_reproduce_instructions",
            status=ToolStatus.ERROR,
            error=f"{rel_pointer} does not exist at revision {payload.rev}: {show.stderr.strip()}",
            source=_source("git", ["show", f"{payload.rev}:{rel_pointer}"]),
        )

    steps = [
        f"git checkout {payload.rev} -- {rel_pointer}",
        f"dvc checkout {rel_pointer}",
        f"# verify: the hash in {rel_pointer} should read {recorded_hash or '<recorded hash>'}",
    ]
    return ToolResult(
        tool="dvc_reproduce_instructions",
        status=ToolStatus.OK,
        data={
            "revision": payload.rev,
            "tracked_path": payload.path,
            "recorded_hash": recorded_hash,
            "steps": steps,
            "note": "dvc checkout pulls the recorded content from the configured remote into the working tree.",
        },
        source=_source("git", ["show", f"{payload.rev}:{rel_pointer}"]),
    )


def register(registry: ToolRegistry) -> None:
    """Add the DVC tools to the registry."""
    registry.register(
        ToolSpec(
            name="dvc_list_tracked_datasets",
            description="List datasets tracked by DVC in the repository, with their recorded content hashes.",
            input_model=EmptyInput,
            module=MODULE,
        ),
        list_tracked_datasets,
    )
    registry.register(
        ToolSpec(
            name="dvc_dataset_version",
            description=(
                "Get the current version of a tracked dataset: its DVC content hash plus the Git commit "
                "and tag that recorded it."
            ),
            input_model=DatasetPathInput,
            module=MODULE,
        ),
        dataset_version,
    )
    registry.register(
        ToolSpec(
            name="dvc_dataset_status",
            description="Detect whether tracked data has changed relative to what DVC recorded.",
            input_model=EmptyInput,
            module=MODULE,
        ),
        dataset_status,
    )
    registry.register(
        ToolSpec(
            name="dvc_compare_versions",
            description="Compare DVC-tracked data between two Git revisions, branches or tags.",
            input_model=DatasetCompareInput,
            module=MODULE,
        ),
        compare_versions,
    )
    registry.register(
        ToolSpec(
            name="dvc_reproduce_instructions",
            description="Explain the exact commands that restore a previous dataset version.",
            input_model=DatasetReproduceInput,
            module=MODULE,
        ),
        reproduce_instructions,
    )
