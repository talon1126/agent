"""Synchronize DEV_SPEC.md into auto-coder reference files."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
SKILL_DIR = Path(__file__).resolve().parents[1]
REFERENCE_DIR = SKILL_DIR / "references"

DEFAULT_SPEC_CANDIDATES = (
    ROOT / "services" / "ai-service" / "rag" / "DEV_SPEC.md",
    ROOT / "DEV_SPEC.md",
)

SECTION_FILES = {
    "项目概述": ("01-overview.md", "项目概述"),
    "核心特点": ("02-features.md", "功能规范"),
    "技术选型": ("03-tech-stack.md", "技术栈与依赖"),
    "测试方案": ("04-testing.md", "测试规范"),
    "系统架构与模块设计": ("05-architecture.md", "架构与模块设计"),
    "项目排期": ("06-schedule.md", "任务计划与状态"),
    "开发规范": ("07-development-rules.md", "开发与提交规范"),
}


def find_spec(explicit_path: str | None) -> Path:
    """Find the DEV_SPEC.md file from an explicit path or known project locations."""
    if explicit_path:
        spec_path = Path(explicit_path)
        if not spec_path.is_absolute():
            spec_path = ROOT / spec_path
        if spec_path.exists():
            return spec_path
        raise FileNotFoundError(f"DEV_SPEC not found: {spec_path}")

    for candidate in DEFAULT_SPEC_CANDIDATES:
        if candidate.exists():
            return candidate
    matches = sorted(ROOT.rglob("DEV_SPEC.md"))
    if matches:
        return matches[0]
    raise FileNotFoundError("No DEV_SPEC.md found in the workspace")


def split_sections(text: str) -> dict[str, str]:
    """Split DEV_SPEC.md by level-2 numbered Chinese section headings."""
    heading_pattern = re.compile(r"^##\s+\d+\.\s+(.+?)\s*$", re.MULTILINE)
    matches = list(heading_pattern.finditer(text))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        title = match.group(1).strip()
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[title] = text[start:end].strip() + "\n"
    return sections


def write_reference(file_name: str, label: str, spec_path: Path, content: str, force: bool) -> None:
    """Write one synchronized reference file."""
    REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
    output_path = REFERENCE_DIR / file_name
    if output_path.exists() and not force:
        existing = output_path.read_text(encoding="utf-8")
        if existing.startswith("<!-- synced-from:"):
            pass
    header = (
        f"<!-- synced-from: {spec_path.relative_to(ROOT)} -->\n"
        f"<!-- reference: {label} -->\n\n"
    )
    output_path.write_text(header + content, encoding="utf-8")


def sync_spec(spec_path: Path, force: bool) -> list[Path]:
    """Synchronize known DEV_SPEC sections into references."""
    text = spec_path.read_text(encoding="utf-8")
    sections = split_sections(text)
    written: list[Path] = []
    for section_title, (file_name, label) in SECTION_FILES.items():
        content = sections.get(section_title)
        if not content:
            content = f"# {label}\n\nDEV_SPEC.md does not contain section: {section_title}\n"
        write_reference(file_name, label, spec_path, content, force)
        written.append(REFERENCE_DIR / file_name)
    return written


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Sync DEV_SPEC.md to auto-coder references.")
    parser.add_argument("--spec", help="Path to DEV_SPEC.md. Defaults to known project locations.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing synchronized references.")
    args = parser.parse_args()

    spec_path = find_spec(args.spec)
    written = sync_spec(spec_path, args.force)
    for path in written:
        print(path.relative_to(ROOT))


if __name__ == "__main__":
    main()
