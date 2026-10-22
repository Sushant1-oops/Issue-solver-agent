"""Apply model-proposed search/replace patches to the cloned repository."""
from __future__ import annotations

from pathlib import Path

from .schemas import Patch


def _safe_rel(path: str) -> str | None:
    cleaned = (path or "").strip().replace("\\", "/").lstrip("/")
    if not cleaned or cleaned.startswith("~"):
        return None
    parts = cleaned.split("/")
    if any(part in ("..", ".") for part in parts):
        return None
    return cleaned


def _fuzzy_replace(content: str, old: str, new: str) -> str | None:
    """Whitespace-tolerant fallback: match trimmed lines, replace the window."""
    cur_lines = content.splitlines()
    old_lines = [ln.strip() for ln in old.strip("\n").splitlines()]
    if not old_lines:
        return None
    new_lines = new.splitlines()
    width = len(old_lines)
    for i in range(len(cur_lines) - width + 1):
        window = [ln.strip() for ln in cur_lines[i : i + width]]
        if window == old_lines:
            out = cur_lines[:i] + new_lines + cur_lines[i + width :]
            ending = "\n" if content.endswith("\n") else ""
            return "\n".join(out) + ending
    return None


def apply_patches(repo_dir: str, patches: list[Patch]) -> tuple[list[dict], list[dict]]:
    """Apply patches sequentially. Returns (applied_infos, failures)."""
    applied: list[dict] = []
    failed: list[dict] = []

    for patch in patches:
        rel = _safe_rel(patch.path)
        if rel is None:
            failed.append({"path": patch.path, "reason": "unsafe or empty path"})
            continue
        full = Path(repo_dir) / rel
        action = (patch.action or "replace").strip().lower()

        try:
            if action == "delete":
                if not full.exists():
                    failed.append({"path": rel, "reason": "file not found"})
                    continue
                old_content = full.read_text(encoding="utf-8", errors="replace")
                full.unlink()
                applied.append(
                    {"path": rel, "action": "delete", "old_content": old_content, "new_content": ""}
                )

            elif action == "create":
                full.parent.mkdir(parents=True, exist_ok=True)
                old_content = ""
                if full.exists():
                    old_content = full.read_text(encoding="utf-8", errors="replace")
                full.write_text(patch.new or "", encoding="utf-8")
                applied.append(
                    {
                        "path": rel,
                        "action": "create",
                        "old_content": old_content,
                        "new_content": patch.new or "",
                    }
                )

            else:  # replace
                if not full.exists():
                    failed.append({"path": rel, "reason": "file not found"})
                    continue
                if not patch.old:
                    failed.append({"path": rel, "reason": "empty 'old' snippet"})
                    continue
                current = full.read_text(encoding="utf-8", errors="replace")
                if patch.old in current:
                    updated = current.replace(patch.old, patch.new)
                else:
                    updated = _fuzzy_replace(current, patch.old, patch.new)
                if updated is None:
                    failed.append({"path": rel, "reason": "'old' snippet not found in file"})
                    continue
                if updated == current:
                    failed.append({"path": rel, "reason": "patch produced no change"})
                    continue
                full.write_text(updated, encoding="utf-8")
                applied.append(
                    {
                        "path": rel,
                        "action": "replace",
                        "old_content": current,
                        "new_content": updated,
                    }
                )
        except Exception as exc:  # noqa: BLE001 - report and continue
            failed.append({"path": rel, "reason": f"{type(exc).__name__}: {exc}"})

    return applied, failed
