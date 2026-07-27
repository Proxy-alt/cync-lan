"""A readable view of one decompiled file.

Decompiler output is roughly 30% noise by line count: `@Metadata` blobs that
are unreadable escaped protobuf, `/* JADX INFO: renamed from: r */` above every
member, and `/* JADX DEBUG: ... */` casts commentary. Stripping those and
adding hex for the byte literals is the difference between a file you scroll
past and one you can read.

Never modifies the tree - this writes to stdout.
"""

from __future__ import annotations

import re

from . import hexify, kmeta

# JADX bookkeeping comments. `renamed from` is kept (compacted) because it is
# the only surviving trace of the pre-R8 member name.
_JADX_RENAMED = re.compile(r"/\*\s*JADX INFO: renamed from: (\S+)\s*\*/")
_JADX_NOISE = re.compile(
    r"^\s*/\*\s*JADX (INFO|DEBUG|WARN)[^*]*(\*(?!/)[^*]*)*\*/\s*$"
)
_ANNOTATION_NOISE = re.compile(r"^\s*@(NotNull|Nullable|Override)\b")
# The escaped-protobuf blob. Its readable half is already lifted into the
# header by kmeta, so the line itself is pure noise.
_METADATA = re.compile(r"^\s*@Metadata\(")
_SOURCE_NOTE = re.compile(r"cync-lan reverse-engineering note", re.IGNORECASE)


def render(
    text: str,
    *,
    hex_bytes: bool = True,
    inline_hex: bool = False,
    strip_noise: bool = True,
    show_names: bool = True,
    keep_annotations: bool = False,
) -> str:
    header: list[str] = []
    if show_names:
        lines = kmeta.summary(text)
        if lines:
            header = [
                "// ---- recovered Kotlin names (from @Metadata; order is a hint, not a proof) ----",
                *[f"// {l}" for l in lines],
                "// " + "-" * 76,
                "",
            ]

    if inline_hex:
        text = hexify.inline(text)

    out: list[str] = []
    pending_rename: str | None = None
    for line in text.splitlines():
        if strip_noise:
            m = _JADX_RENAMED.search(line)
            if m and not line.strip().replace(m.group(0), "").strip():
                pending_rename = m.group(1)
                continue
            if _JADX_NOISE.match(line) or _METADATA.match(line):
                continue
            if line.lstrip().startswith("import kotlin.Metadata;"):
                continue
            if not keep_annotations and _ANNOTATION_NOISE.match(line):
                continue
            if line.strip() == "" and out and out[-1].strip() == "":
                continue

        if hex_bytes and not inline_hex:
            line = hexify.annotate_line(line)
        if pending_rename:
            line = f"{line}  // was: {pending_rename}"
            pending_rename = None
        if _SOURCE_NOTE.search(line):
            line = f">>> {line.lstrip()}" if line.strip().startswith(("//", "*")) else line
        out.append(line)

    return "\n".join(header + out)
