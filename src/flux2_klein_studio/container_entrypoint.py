"""Seed pinned full-image assets without replacing other cached downloads."""

import os
from pathlib import Path
import shutil
import sys
import tempfile

from .build_info import offline_default


def seed_cache(source, destination):
    source, destination = Path(source), Path(destination)
    if not source.is_dir():
        return 0
    source = source.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    destination = destination.resolve()
    copied = 0
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if ".locks" in relative.parts:
            continue
        target = destination / relative
        if not path.resolve().is_relative_to(
            source
        ) or not target.resolve().is_relative_to(destination):
            raise ValueError(f"Cache link escapes its root: {relative}")
        if path.is_symlink():
            link = os.readlink(path)
            if target.is_symlink() and os.readlink(target) == link:
                continue
            if (
                target.is_file()
                and not target.is_symlink()
                and target.stat().st_size == path.stat().st_size
            ):
                continue
            if target.exists() or target.is_symlink():
                raise ValueError(f"Conflicting cache entry: {relative}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(link)
        elif path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            if (
                target.is_file()
                and target.stat().st_size == path.stat().st_size
                and (
                    "blobs" in relative.parts
                    or target.read_bytes() == path.read_bytes()
                )
            ):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                dir=target.parent, delete=False
            ) as pending:
                temporary = Path(pending.name)
            try:
                shutil.copyfile(path, temporary)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
            copied += 1
    return copied


def main():
    arguments = sys.argv[1:] or ["serve"]
    if arguments[0] in {
        "serve",
        "generate",
        "benchmark",
        "memory",
        "seed-test",
        "resolution-sweep",
        "download",
    }:
        count = seed_cache(
            "/opt/klein-models/huggingface",
            os.getenv("HF_HOME", "/app/persistent/huggingface"),
        )
        if count:
            print(f"Baked model cache ready ({count} files copied).", flush=True)
    if offline_default():
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
    elif os.getenv("STUDIO_OFFLINE") == "0":
        os.environ["HF_HUB_OFFLINE"] = "0"
        os.environ["TRANSFORMERS_OFFLINE"] = "0"
    os.execv(sys.executable, [sys.executable, "-m", "flux2_klein_studio", *arguments])


if __name__ == "__main__":
    main()
