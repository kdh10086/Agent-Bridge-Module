from __future__ import annotations

import fnmatch
import shutil
from pathlib import Path

from pydantic import BaseModel, ConfigDict


WORKSPACE_SUBDIRS = ["state", "queue", "inbox", "outbox", "reports", "reviews", "logs"]


class PortableInstallPlan(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    target: Path
    source_bridge_dir: Path
    target_bridge_dir: Path
    include_agents_snippet: bool
    source_agents_snippet: Path | None
    target_agents_snippet: Path | None
    would_create: list[Path]
    would_overwrite: list[Path]
    blocked_reason: str | None = None

    @property
    def blocked(self) -> bool:
        return self.blocked_reason is not None


class PortableVerifyResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    target: Path
    required_present: list[Path]
    missing_required: list[Path]
    executable_present: list[Path]
    missing_executable: list[Path]
    agents_snippet_present: bool

    @property
    def ok(self) -> bool:
        return not self.missing_required and not self.missing_executable


def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_source_root() -> Path:
    return repository_root() / "portable_module"


def _blocked_plan(
    target: Path,
    source_bridge_dir: Path,
    include_agents_snippet: bool,
    source_agents_snippet: Path | None,
    reason: str,
) -> PortableInstallPlan:
    target_bridge_dir = target / ".agent-bridge"
    target_agents = target / "AGENTS.agent-bridge.snippet.md" if include_agents_snippet else None
    return PortableInstallPlan(
        target=target,
        source_bridge_dir=source_bridge_dir,
        target_bridge_dir=target_bridge_dir,
        include_agents_snippet=include_agents_snippet,
        source_agents_snippet=source_agents_snippet,
        target_agents_snippet=target_agents,
        would_create=[],
        would_overwrite=[],
        blocked_reason=reason,
    )


def _is_dangerous_target(target: Path) -> str | None:
    resolved = target.resolve()
    if resolved == Path(resolved.anchor):
        return "Refusing to install into filesystem root."
    if resolved == Path.home().resolve():
        return "Refusing to install into the home directory root."
    return None


def _validate_child(target: Path, child: Path) -> bool:
    try:
        child.resolve().relative_to(target.resolve())
    except ValueError:
        return False
    return True


def calculate_install_plan(
    target: Path,
    *,
    source_root: Path | None = None,
    force: bool = False,
    include_agents_snippet: bool = True,
) -> PortableInstallPlan:
    source_root = source_root or default_source_root()
    source_bridge_dir = source_root / ".agent-bridge"
    source_agents_snippet = source_root / "AGENTS.agent-bridge.snippet.md"
    source_agents: Path | None = source_agents_snippet if source_agents_snippet.exists() else None

    if str(target).strip() == "":
        return _blocked_plan(
            target,
            source_bridge_dir,
            include_agents_snippet,
            source_agents,
            "Target path is empty.",
        )

    target = target.expanduser()
    target_bridge_dir = target / ".agent-bridge"
    target_agents_snippet = target / "AGENTS.agent-bridge.snippet.md" if include_agents_snippet else None

    if not target.exists():
        return _blocked_plan(
            target,
            source_bridge_dir,
            include_agents_snippet,
            source_agents,
            f"Target path does not exist: {target}",
        )
    if not target.is_dir():
        return _blocked_plan(
            target,
            source_bridge_dir,
            include_agents_snippet,
            source_agents,
            f"Target path is not a directory: {target}",
        )
    dangerous_reason = _is_dangerous_target(target)
    if dangerous_reason:
        return _blocked_plan(
            target,
            source_bridge_dir,
            include_agents_snippet,
            source_agents,
            dangerous_reason,
        )
    if not source_bridge_dir.exists() or not source_bridge_dir.is_dir():
        return _blocked_plan(
            target,
            source_bridge_dir,
            include_agents_snippet,
            source_agents,
            f"Source portable module is missing: {source_bridge_dir}",
        )
    if target.resolve() == source_bridge_dir.resolve():
        return _blocked_plan(
            target,
            source_bridge_dir,
            include_agents_snippet,
            source_agents,
            "Target path cannot equal the source portable module path.",
        )
    if target_bridge_dir.resolve() == source_bridge_dir.resolve():
        return _blocked_plan(
            target,
            source_bridge_dir,
            include_agents_snippet,
            source_agents,
            "Target .agent-bridge path cannot equal the source portable module path.",
        )
    if not _validate_child(target, target_bridge_dir):
        return _blocked_plan(
            target,
            source_bridge_dir,
            include_agents_snippet,
            source_agents,
            "Target bridge path would be outside the target directory.",
        )
    if target_agents_snippet and not _validate_child(target, target_agents_snippet):
        return _blocked_plan(
            target,
            source_bridge_dir,
            include_agents_snippet,
            source_agents,
            "Target AGENTS snippet path would be outside the target directory.",
        )
    if target_bridge_dir.is_symlink():
        return _blocked_plan(
            target,
            source_bridge_dir,
            include_agents_snippet,
            source_agents,
            "Refusing to overwrite symlinked .agent-bridge path.",
        )
    if target_agents_snippet and target_agents_snippet.is_symlink():
        return _blocked_plan(
            target,
            source_bridge_dir,
            include_agents_snippet,
            source_agents,
            "Refusing to overwrite symlinked AGENTS snippet path.",
        )
    if target_bridge_dir.exists() and not force:
        return _blocked_plan(
            target,
            source_bridge_dir,
            include_agents_snippet,
            source_agents,
            "Target already contains .agent-bridge/. Use --force to overwrite.",
        )
    if target_agents_snippet and target_agents_snippet.exists() and not force:
        return _blocked_plan(
            target,
            source_bridge_dir,
            include_agents_snippet,
            source_agents,
            "Target already contains AGENTS.agent-bridge.snippet.md. Use --force to overwrite.",
        )
    if include_agents_snippet and source_agents is None:
        return _blocked_plan(
            target,
            source_bridge_dir,
            include_agents_snippet,
            source_agents,
            f"Source AGENTS snippet is missing: {source_agents_snippet}",
        )

    would_create = []
    would_overwrite = []
    if target_bridge_dir.exists():
        would_overwrite.append(target_bridge_dir)
    else:
        would_create.append(target_bridge_dir)
    if target_agents_snippet:
        if target_agents_snippet.exists():
            would_overwrite.append(target_agents_snippet)
        else:
            would_create.append(target_agents_snippet)

    return PortableInstallPlan(
        target=target,
        source_bridge_dir=source_bridge_dir,
        target_bridge_dir=target_bridge_dir,
        include_agents_snippet=include_agents_snippet,
        source_agents_snippet=source_agents,
        target_agents_snippet=target_agents_snippet,
        would_create=would_create,
        would_overwrite=would_overwrite,
    )


def _copy_ignore(path: str, names: list[str]) -> set[str]:
    current = Path(path)
    ignored: set[str] = set()
    for name in names:
        if name in {"__pycache__", ".DS_Store"} or fnmatch.fnmatch(name, "*.pyc"):
            ignored.add(name)
    if current.name in WORKSPACE_SUBDIRS and current.parent.name == "workspace":
        for name in names:
            if name != ".gitkeep":
                ignored.add(name)
    return ignored


def _ensure_clean_workspace(target_bridge_dir: Path) -> None:
    workspace = target_bridge_dir / "workspace"
    for subdir in WORKSPACE_SUBDIRS:
        path = workspace / subdir
        path.mkdir(parents=True, exist_ok=True)
        gitkeep = path / ".gitkeep"
        if not gitkeep.exists():
            gitkeep.write_text("", encoding="utf-8")


def install_portable(
    target: Path,
    *,
    source_root: Path | None = None,
    dry_run: bool = False,
    force: bool = False,
    include_agents_snippet: bool = True,
) -> PortableInstallPlan:
    plan = calculate_install_plan(
        target,
        source_root=source_root,
        force=force,
        include_agents_snippet=include_agents_snippet,
    )
    if plan.blocked or dry_run:
        return plan

    if plan.target_bridge_dir.exists():
        if plan.target_bridge_dir.is_dir():
            shutil.rmtree(plan.target_bridge_dir)
        else:
            plan.target_bridge_dir.unlink()

    shutil.copytree(
        plan.source_bridge_dir,
        plan.target_bridge_dir,
        ignore=_copy_ignore,
        symlinks=False,
    )
    _ensure_clean_workspace(plan.target_bridge_dir)

    if plan.include_agents_snippet and plan.source_agents_snippet and plan.target_agents_snippet:
        shutil.copy2(plan.source_agents_snippet, plan.target_agents_snippet)

    return plan


def verify_portable(target: Path) -> PortableVerifyResult:
    target = target.expanduser()
    bridge = target / ".agent-bridge"
    required = [
        bridge / "README.md",
        bridge / "config.yaml",
        bridge / "workspace",
        bridge / "docs" / "SAFETY.md",
    ]
    executable = [
        bridge / "scripts" / "self_test.sh",
        bridge / "scripts" / "ingest_review.sh",
        bridge / "scripts" / "ingest_ci.sh",
        bridge / "scripts" / "dispatch_next.sh",
    ]
    required_present = [path for path in required if path.exists()]
    missing_required = [path for path in required if not path.exists()]
    executable_present = [path for path in executable if path.exists() and path.stat().st_mode & 0o111]
    missing_executable = [
        path for path in executable if not path.exists() or not (path.stat().st_mode & 0o111)
    ]
    return PortableVerifyResult(
        target=target,
        required_present=required_present,
        missing_required=missing_required,
        executable_present=executable_present,
        missing_executable=missing_executable,
        agents_snippet_present=(target / "AGENTS.agent-bridge.snippet.md").exists(),
    )


def format_install_plan(plan: PortableInstallPlan, *, dry_run: bool) -> str:
    title = "Portable install dry-run" if dry_run else "Portable install plan"
    lines = [
        title,
        "",
        "Target:",
        f"  {plan.target}",
        "",
        "Source:",
        f"  {plan.source_bridge_dir}",
        "",
    ]
    if plan.blocked_reason:
        lines.extend(["Blocked:", f"  {plan.blocked_reason}", ""])
        return "\n".join(lines)

    lines.append("Would copy:" if dry_run else "Copied:")
    lines.append(f"  {plan.source_bridge_dir} -> {plan.target_bridge_dir}")
    if plan.include_agents_snippet and plan.source_agents_snippet and plan.target_agents_snippet:
        lines.append(f"  {plan.source_agents_snippet} -> {plan.target_agents_snippet}")
    elif not plan.include_agents_snippet:
        lines.append("  AGENTS.agent-bridge.snippet.md skipped by flag.")
    lines.append("")

    if plan.would_create:
        lines.append("Would create:" if dry_run else "Created or refreshed:")
        lines.extend(f"  {path}" for path in plan.would_create)
        lines.append("")
    if plan.would_overwrite:
        lines.append("Would overwrite:" if dry_run else "Overwrote:")
        lines.extend(f"  {path}" for path in plan.would_overwrite)
        lines.append("")
    if dry_run:
        lines.append("No files were modified.")
    return "\n".join(lines)


def format_verify_result(result: PortableVerifyResult) -> str:
    lines = [
        "Portable verification",
        "",
        "Target:",
        f"  {result.target}",
        "",
    ]
    if result.required_present:
        lines.append("Present:")
        lines.extend(f"  {path}" for path in result.required_present)
        lines.append("")
    if result.executable_present:
        lines.append("Executable scripts:")
        lines.extend(f"  {path}" for path in result.executable_present)
        lines.append("")
    if result.missing_required or result.missing_executable:
        lines.append("Missing or invalid:")
        lines.extend(f"  {path}" for path in result.missing_required)
        lines.extend(f"  {path}" for path in result.missing_executable)
        lines.append("")
    lines.append(
        "AGENTS snippet: present"
        if result.agents_snippet_present
        else "AGENTS snippet: missing"
    )
    lines.append("Verification passed." if result.ok else "Verification failed.")
    return "\n".join(lines)
