"""Test ensuring zero emojis exist across the entire codebase and documentation."""

from pathlib import Path
import re


def test_no_emojis_in_repository():
    """Verify that no unicode emojis exist in python files, docs, or config files."""
    emoji_pattern = re.compile(
        "[\U0001F600-\U0001F64F]"  # emoticons
        "|[\U0001F300-\U0001F5FF]"  # symbols & pictographs
        "|[\U0001F680-\U0001F6FF]"  # transport & map symbols
        "|[\U0001F1E0-\U0001F1FF]"  # flags
        "|[\U00002700-\U000027BF]"  # dingbats
        "|[\U0001F900-\U0001F9FF]"  # supplemental symbols
        "|[\U0001FA70-\U0001FAFF]"  # symbols and pictographs extended
        "|[\U00002600-\U000026FF]"  # misc symbols
        "|[\U00002B50]",  # star
        flags=re.UNICODE,
    )

    repo_root = Path(__file__).parent.parent
    violations = []

    # Target file extensions
    extensions = {".py", ".md", ".json", ".yaml", ".yml", ".ini"}

    for path in repo_root.rglob("*"):
        if (
            path.is_file()
            and path.suffix.lower() in extensions
            and ".git" not in path.parts
            and "__pycache__" not in path.parts
            and ".pytest_cache" not in path.parts
        ):
            try:
                content = path.read_text(encoding="utf-8")
                matches = emoji_pattern.findall(content)
                if matches:
                    violations.append(
                        f"{path.relative_to(repo_root)}: found {len(matches)} emoji(s) {matches}"
                    )
            except UnicodeDecodeError:
                pass

    assert not violations, f"Emojis detected in repository files:\n" + "\n".join(
        violations
    )
