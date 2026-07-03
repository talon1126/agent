"""Synchronize project DEV_SPEC.md files into auto-coder reference files."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
SKILL_DIR = Path(__file__).resolve().parents[1]
REFERENCE_DIR = SKILL_DIR / "references"

SPEC_DOMAINS = {
    "talonMart": ROOT / "DEV_SPEC.md",
    "rag": ROOT / "services" / "ai-service" / "rag" / "DEV_SPEC.md",
}

SECTION_FILES = {
    "项目概述": ("01-overview.md", "项目概述"),
    "核心特点": ("02-features.md", "功能规范"),
    "技术选型": ("03-tech-stack.md", "技术栈与依赖"),
    "测试方案": ("04-testing.md", "测试规范"),
    "系统架构与模块设计": ("05-architecture.md", "架构与模块设计"),
    "项目排期": ("06-schedule.md", "任务计划与状态"),
    "开发规范": ("07-development-rules.md", "开发与提交规范"),
}


def infer_domain(spec_path: Path) -> str:
    """Infer the reference domain for an explicit DEV_SPEC path."""
    resolved = spec_path.resolve()
    for domain, candidate in SPEC_DOMAINS.items():
        if resolved == candidate.resolve():
            return domain
    if "rag" in {part.lower() for part in resolved.parts}:
        return "rag"
    return "talonMart"


def find_spec(explicit_path: str | None, domain: str) -> Path:
    """Find the DEV_SPEC.md file for one selected reference domain."""
    if explicit_path:
        spec_path = Path(explicit_path)
        if not spec_path.is_absolute():
            spec_path = ROOT / spec_path
        if spec_path.exists():
            return spec_path
        raise FileNotFoundError(f"DEV_SPEC not found: {spec_path}")

    candidate = SPEC_DOMAINS[domain]
    if candidate.exists():
        return candidate
    raise FileNotFoundError(f"No DEV_SPEC.md found for domain '{domain}': {candidate}")


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


def normalize_reference_content(content: str) -> str:
    """Remove line-end whitespace before writing generated reference files."""
    return "\n".join(line.rstrip() for line in content.splitlines()).rstrip() + "\n"


def write_reference(
    reference_dir: Path,
    file_name: str,
    label: str,
    spec_path: Path,
    content: str,
    force: bool,
) -> None:
    """Write one synchronized reference file."""
    reference_dir.mkdir(parents=True, exist_ok=True)
    output_path = reference_dir / file_name
    if output_path.exists() and not force:
        existing = output_path.read_text(encoding="utf-8")
        if existing.startswith("<!-- synced-from:"):
            pass
    header = (
        f"<!-- synced-from: {spec_path.relative_to(ROOT)} -->\n"
        f"<!-- reference: {label} -->\n\n"
    )
    output_path.write_text(header + normalize_reference_content(content), encoding="utf-8")


def sync_spec(domain: str, spec_path: Path, force: bool) -> list[Path]:
    """Synchronize known DEV_SPEC sections into references."""
    text = spec_path.read_text(encoding="utf-8")
    sections = split_sections(text)
    written: list[Path] = []
    reference_dir = REFERENCE_DIR / domain
    for section_title, (file_name, label) in SECTION_FILES.items():
        content = sections.get(section_title)
        if not content:
            content = f"# {label}\n\nDEV_SPEC.md does not contain section: {section_title}\n"
        write_reference(reference_dir, file_name, label, spec_path, content, force)
        written.append(reference_dir / file_name)
    return written


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Sync DEV_SPEC.md files to auto-coder references.")
    parser.add_argument(
        "--domain",
        choices=[*SPEC_DOMAINS.keys(), "all"],
        default="all",
        help="Reference domain to synchronize. Defaults to all domains.",
    )
    parser.add_argument("--spec", help="Path to DEV_SPEC.md. With no --domain, the domain is inferred.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing synchronized references.")
    args = parser.parse_args()

    if args.spec:
        spec_path = find_spec(args.spec, infer_domain(Path(args.spec)))
        domain = args.domain if args.domain != "all" else infer_domain(spec_path)
        domains = [domain]
        explicit_specs = {domain: spec_path}
    else:
        domains = list(SPEC_DOMAINS) if args.domain == "all" else [args.domain]
        explicit_specs = {}

    for domain in domains:
        spec_path = explicit_specs.get(domain) or find_spec(None, domain)
        written = sync_spec(domain, spec_path, args.force)
        for path in written:
            print(path.relative_to(ROOT))


if __name__ == "__main__":
    main()
