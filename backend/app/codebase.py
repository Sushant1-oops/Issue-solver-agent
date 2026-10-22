"""Token-aware codebase inspection: file map, capped reads, regex search.

Everything here is local and fast — no embeddings — to keep a job inside the
2-3 minute budget and keep LLM prompts small.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from .config import settings

IGNORE_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
    ".next", ".nuxt", "target", ".idea", ".vscode", "coverage", ".pytest_cache",
    ".mypy_cache", ".tox", "vendor", ".gradle", ".cache", "coverage", "htmlcov",
}

TEXT_EXTS = {
    ".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".java", ".go", ".rs",
    ".rb", ".php", ".c", ".h", ".cpp", ".hpp", ".cc", ".cs", ".swift", ".kt",
    ".kts", ".scala", ".sh", ".bash", ".zsh", ".ps1", ".sql", ".html", ".htm",
    ".css", ".scss", ".less", ".vue", ".svelte", ".json", ".yml", ".yaml",
    ".toml", ".ini", ".cfg", ".md", ".mdx", ".txt", ".xml", ".example", ".proto",
    ".graphql", ".tf", ".docker", ".po", ".tpl",
}

SPECIAL_NAMES = {
    "Dockerfile", "Makefile", "Gemfile", "Rakefile", "Procfile", "Vagrantfile",
    ".gitignore", ".dockerignore", ".env.example", ".editorconfig", "LICENSE",
}

MAX_FILE_BYTES = 300_000
_LOCKFILE_RE = re.compile(r"(\.min\.[a-z]+$|lock\.(json|yaml|txt)$|\.map$)")


def _is_text_file(name: str) -> bool:
    if name in SPECIAL_NAMES:
        return True
    if _LOCKFILE_RE.search(name) or name.startswith("."):
        return False
    ext = os.path.splitext(name)[1].lower()
    return ext in TEXT_EXTS


def list_text_files(repo_dir: str) -> list[str]:
    """Repo-relative POSIX paths of readable source files (sorted)."""
    out: list[str] = []
    root = Path(repo_dir)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if d not in IGNORE_DIRS and not d.startswith(".")
        )
        for fname in sorted(filenames):
            if not _is_text_file(fname):
                continue
            full = Path(dirpath) / fname
            try:
                if full.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            out.append(full.relative_to(root).as_posix())
    return out


def _fmt_size(num: int) -> str:
    if num < 1024:
        return f"{num}B"
    if num < 1024 * 1024:
        return f"{num / 1024:.1f}KB"
    return f"{num / (1024 * 1024):.1f}MB"


def _render_tree(paths: list[str], sizes: dict[str, int]) -> list[str]:
    lines: list[str] = []
    stack: list[str] = []
    for rel in paths:
        parts = rel.split("/")
        dirs, fname = parts[:-1], parts[-1]
        common = 0
        while common < len(dirs) and common < len(stack) and dirs[common] == stack[common]:
            common += 1
        for depth in range(common, len(dirs)):
            lines.append(f"{'  ' * depth}{dirs[depth]}/")
        stack = dirs
        size = sizes.get(rel, 0)
        lines.append(f"{'  ' * len(dirs)}{fname}  ({_fmt_size(size)})")
    return lines


def detect_project_markers(repo_dir: str) -> str:
    p = Path(repo_dir)
    markers = []
    checks = [
        ("package.json", "javascript/node"),
        ("pyproject.toml", "python"),
        ("setup.py", "python"),
        ("requirements.txt", "python"),
        ("go.mod", "go"),
        ("Cargo.toml", "rust"),
        ("pom.xml", "java/maven"),
        ("build.gradle", "java/gradle"),
        ("Gemfile", "ruby"),
        ("composer.json", "php"),
    ]
    for fname, label in checks:
        if (p / fname).exists():
            markers.append(label)
    if (p / "tests").is_dir() or (p / "test").is_dir():
        markers.append("has test directory")
    return ", ".join(sorted(set(markers))) or "unknown"


def build_repo_map(repo_dir: str) -> str:
    """Indented file tree with size caps, used as compact LLM context."""
    files = list_text_files(repo_dir)
    cap = settings.repo_map_max_entries
    shown = files[:cap]
    sizes: dict[str, int] = {}
    for rel in shown:
        try:
            sizes[rel] = (Path(repo_dir) / rel).stat().st_size
        except OSError:
            sizes[rel] = 0
    lines = _render_tree(shown, sizes)
    header = [
        f"Total readable files: {len(files)}",
        f"Project markers: {detect_project_markers(repo_dir)}",
    ]
    if len(files) > cap:
        lines.append(f"... and {len(files) - cap} more files not shown")
    return "\n".join(header + lines)


def read_file_capped(repo_dir: str, rel: str, cap: int | None = None) -> str:
    """Read a file, truncating the middle if larger than the char budget."""
    cap = cap or settings.max_file_chars
    text = (Path(repo_dir) / rel).read_text(encoding="utf-8", errors="replace")
    if len(text) <= cap:
        return text
    half = cap // 2
    return (
        text[:half]
        + f"\n\n... [TRUNCATED {len(text) - cap} CHARACTERS] ...\n\n"
        + text[-half:]
    )


def search_files(
    repo_dir: str, files: list[str], pattern: str, max_matches: int = 25, per_file: int = 5
) -> str:
    """Regex grep across the indexed file list. Returns 'path:line: snippet' lines."""
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error:
        return f"(invalid regex: {pattern})"
    results: list[str] = []
    for rel in files:
        try:
            text = (Path(repo_dir) / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        count = 0
        for lineno, line in enumerate(text.splitlines(), start=1):
            if count >= per_file:
                break
            if rx.search(line):
                results.append(f"{rel}:{lineno}: {line.strip()[:200]}")
                count += 1
                if len(results) >= max_matches:
                    break
        if len(results) >= max_matches:
            break
    blob = "\n".join(results) if results else "(no matches)"
    return blob[:5000]


def resolve_candidate(cand: str, files: list[str]) -> str | None:
    """Map a model-proposed path to a real file path (exact, then basename)."""
    if not cand:
        return None
    key = cand.replace("\\", "/").strip().rstrip("/")
    while key.startswith("./"):
        key = key[2:]
    key = key.lstrip("/")
    lookup = {f.lower(): f for f in files}
    if key.lower() in lookup:
        return lookup[key.lower()]
    base = key.rsplit("/", 1)[-1].lower()
    if not base:
        return None
    for f in files:
        low = f.lower()
        if low == base or low.endswith("/" + base):
            return f
    return None
