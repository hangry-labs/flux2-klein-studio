"""Fetch the reviewed model and Linux CUDA kernel without loading model weights."""

import hashlib
import json
from pathlib import Path

from huggingface_hub import HfApi

from flux2_klein_studio.model_catalog import (
    KERNEL_REPOSITORY,
    KERNEL_REVISION,
    KERNEL_VARIANT,
    KERNEL_VERSION,
    MODEL_ID,
    MODEL_REVISION,
)


def main():
    api = HfApi(token=False)
    if (
        api.repo_info(
            KERNEL_REPOSITORY, repo_type="kernel", revision=KERNEL_VERSION
        ).sha
        != KERNEL_REVISION
    ):
        raise ValueError("Kernel version moved; review and update the pinned stack.")
    root = Path(api.snapshot_download(MODEL_ID, revision=MODEL_REVISION, token=False))
    kernel = Path(
        api.snapshot_download(
            KERNEL_REPOSITORY,
            repo_type="kernel",
            revision=KERNEL_REVISION,
            token=False,
            allow_patterns=[
                f"build/{KERNEL_VARIANT}/*",
                "*LICENSE*",
                "*NOTICE*",
                "README*",
            ],
        )
    )
    # The runtime requests v2. Downloads by commit alone do not create this ref.
    ref = kernel.parent.parent / "refs" / KERNEL_VERSION
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_text(KERNEL_REVISION, encoding="utf-8")
    files = []
    for kind, snapshot in (("model", root), ("kernel", kernel)):
        for path in sorted(snapshot.rglob("*")):
            if path.is_file():
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                files.append(
                    dict(
                        kind=kind,
                        path=path.relative_to(snapshot).as_posix(),
                        size=path.stat().st_size,
                        sha256=digest,
                    )
                )
    output = Path("/opt/klein-models/manifest.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            dict(
                model=MODEL_ID,
                model_revision=MODEL_REVISION,
                kernel=KERNEL_REPOSITORY,
                kernel_revision=KERNEL_REVISION,
                kernel_variant=KERNEL_VARIANT,
                files=files,
            ),
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
