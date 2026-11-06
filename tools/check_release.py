"""Check version identity and public distribution prerequisites without mutation."""

import argparse
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]


def check(root=ROOT, publishing=False):
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+(?:\.\d+)?(?:-snapshot)?", version):
        raise ValueError("Invalid VERSION")
    runtime = (root / "src/flux2_klein_studio/__init__.py").read_text(encoding="utf-8")
    if f'__version__ = "{version}"' not in runtime:
        raise ValueError("VERSION and runtime version differ")
    for file in ("NOTICE", "THIRD_PARTY.md", "tools/runtime_sources.toml"):
        if not (root / file).is_file():
            raise ValueError("Missing distribution input: " + file)
    if publishing and not (root / "LICENSE").is_file():
        raise ValueError(
            "Choose the application's LICENSE before public container publication."
        )
    if version.endswith("-snapshot"):
        # Validate the actual public document transition as well as fixture tests.
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "klein_release", root / "tools/release.py"
        )
        release = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(release)
        release.prepare(
            {file: (root / file).read_text(encoding="utf-8") for file in release.FILES}
        )
    print("Version and distribution inputs checked: " + version)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publishing", action="store_true")
    try:
        check(publishing=parser.parse_args().publishing)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error
