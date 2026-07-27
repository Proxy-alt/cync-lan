"""Where things live in this tree, and which packages are worth reading.

The decompile is ~24k Java files, but only a small fraction is Cync's own code.
Everything here is about drawing that line so the other tools can ignore the
15k files of vendored SDKs.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# --- locating the tree -------------------------------------------------------


def find_root(start: Path | None = None) -> Path:
    """Return the decompile root (the dir containing `sources/`).

    Honours $CYNCDEC_ROOT, else walks up from this file, else from cwd.
    """
    env = os.environ.get("CYNCDEC_ROOT")
    if env:
        p = Path(env).expanduser().resolve()
        if (p / "sources").is_dir():
            return p
        raise SystemExit(f"CYNCDEC_ROOT={p} has no sources/ dir")

    # $PWD is the shell's *logical* cwd, which preserves symlinks; os.getcwd()
    # resolves them. That matters when tools/ is a symlink into a checkout of
    # the research branch: the resolved path sits outside the decompile tree,
    # so only the logical one can find sources/.
    logical = os.environ.get("PWD")
    candidates = [start, Path(logical) if logical else None,
                  Path(__file__).resolve(), Path.cwd().resolve()]
    for base in filter(None, candidates):
        for cand in [base, *base.parents]:
            if (cand / "sources").is_dir():
                return cand
    raise SystemExit("could not find the decompile root (no sources/ dir found); set $CYNCDEC_ROOT")


def sources(root: Path | None = None) -> Path:
    return (root or find_root()) / "sources"


# --- app vs vendor -----------------------------------------------------------

# Cync's own code. Paths are relative to sources/.
APP_ROOTS: dict[str, str] = {
    "com/gelighting/cbygekit": "ge-sdk: the mesh/device/protocol SDK - the interesting one",
    "com/savantsystems/oneapp": "the Cync app itself (UI, viewmodels, navigation)",
    "com/savantsystems/gesdk": "thin app-side glue onto ge-sdk",
}

# Third-party SDKs Cync bundles that still touch the protocol, so worth reading
# when a trail leads into them - but they are not Cync-authored.
NEAR_APP_ROOTS: dict[str, str] = {
    "io/xlink/wifi": "Xlink WiFi SDK - the cloud/UDP transport ge-sdk's Xlink path sits on",
    "com/telink": "Telink BLE mesh SDK (if present in this build)",
}

# Everything else is vendor noise for our purposes. These are listed explicitly
# rather than inferred so that a new top-level package shows up as "unclassified"
# instead of being silently skipped.
VENDOR_ROOTS: dict[str, str] = {
    "com/savantsystems/yisdk": "Yi camera SDK",
    "com/savantsystems/tuyasdk": "Tuya SDK glue",
    "com/thingclips": "Tuya (rebranded ThingClips) SDK",
    "com/tutk": "TUTK P2P video",
    "com/p2p": "P2P video",
    "com/xiaoyi": "Xiaoyi camera SDK",
    "com/ants360": "Ants360 camera SDK",
    "com/xiaomi": "Xiaomi camera SDK",
    "com/tnp": "camera transport",
    "com/video": "video codec glue",
    "com/decoder": "video decoder",
    "com/encoder": "video encoder",
    "com/audio": "audio codec glue",
    "com/aac": "AAC codec",
    "com/freq": "audio freq analysis",
    "com/sinaapp": "misc vendor",
    "com/example": "leftover sample code",
    "com/airbnb": "Lottie",
    "com/alibaba": "fastjson",
    "com/bumptech": "Glide",
    "com/facebook": "Facebook SDK",
    "com/github": "kotlin-result",
    "com/google": "Google/Firebase/Guava/gson/ML Kit/ZXing",
    "com/tuya": "Tuya",
    "com/thing": "Tuya",
    "com/savantsystems/gesdk/p018di": "Hilt-generated DI",
    "chip": "Matter/CHIP SDK (dead weight - see sources/chip/CYNC_LAN_FINDINGS.md)",
    "androidx": "AndroidX",
    "android": "Android framework stubs",
    "kotlin": "Kotlin stdlib",
    "kotlinx": "kotlinx (coroutines/serialization)",
    "okhttp3": "OkHttp",
    "okio": "Okio",
    "retrofit2": "Retrofit",
    "dagger": "Dagger",
    "hilt_aggregated_deps": "Hilt-generated",
    "javax": "javax annotations",
    "io/ktor": "Ktor",
    "io/reactivex": "RxJava",
    "org": "checkerframework/jetbrains/slf4j/chromium/reactivestreams",
    "bolts": "Bolts tasks",
    "timber": "Timber logging",
    "no/nordicsemi": "Nordic BLE library",
    "jni": "JNI stubs",
    "thing": "Tuya security stub",
    # The app's generated resource table. Its package name is the one useful
    # thing in it: the shipped app is `com.ge.cbyge` (JADX escapes the
    # digit-leading segment as `p011ge`).
    "com/p011ge": "generated R.java for app package com.ge.cbyge",
}

# JADX names for unresolvable/synthetic packages: `p073d`, `OooO00o`, etc.
_OBFUSCATED_PREFIXES = ("p0", "OooO")


def classify(rel: str) -> str:
    """Classify a sources/-relative path as 'app', 'near', 'vendor' or 'unknown'."""
    rel = rel.replace(os.sep, "/").lstrip("/")
    for roots, label in ((APP_ROOTS, "app"), (NEAR_APP_ROOTS, "near")):
        for prefix in roots:
            if rel == prefix or rel.startswith(prefix + "/"):
                return label
    for prefix in VENDOR_ROOTS:
        if rel == prefix or rel.startswith(prefix + "/"):
            return "vendor"
    head = rel.split("/", 1)[0]
    if head.startswith(_OBFUSCATED_PREFIXES):
        return "vendor"
    return "unknown"


def is_app(rel: str) -> bool:
    return classify(rel) in ("app", "near")


def fqn_of(path: Path, src: Path) -> str:
    """sources/com/foo/Bar.java -> com.foo.Bar"""
    return str(path.relative_to(src).with_suffix("")).replace(os.sep, ".")


def path_of(fqn: str, src: Path) -> Path:
    return src / (fqn.replace(".", os.sep) + ".java")


# --- jadx-generated identifiers ---------------------------------------------

# jadx's deobfuscator renames identifiers shorter than --deobf-min (default 3)
# to <prefix><counter><original>: `n` becomes `f31596n`, `Ok` becomes
# `C2551Ok`, `d1` becomes `m28775d1`.
#
# The counter is assigned across the whole input and is NOT stable between
# runs - the same command on the same APK produces `C2551Ok` one time and
# `C3120Ok` the next. The suffix is, because it comes from the dex.
#
# So anything that ends up in a citation should use the suffix. Verified
# against 15,683 `renamed from` annotations in the tree; the only divergences
# are cases where jadx qualifies the original ("m.a" rather than "a"), where
# the suffix still gives the correct simple name.
# The suffix must start like a Java identifier - without that anchor,
# `\d{3,}` backtracks and turns a plain `f1234` into `4`.
_JADX_GENERATED = re.compile(r"^([fmC])\d{3,}([A-Za-z_]\w*)$")


def stable_name(name: str) -> str:
    """`f31596n` -> `n`. Returns `name` unchanged if it is not generated."""
    m = _JADX_GENERATED.match(name)
    return m.group(2) if m else name


def is_generated(name: str) -> bool:
    return bool(_JADX_GENERATED.match(name))
