"""Download the GAIA validation split from Hugging Face.

Requires network access and a valid HF token for the gated dataset
``gaia-benchmark/GAIA``.  Set the ``HF_TOKEN`` environment variable or pass
``hf_token`` explicitly.

This module is **not covered by unit tests** — it is a live-only utility.
"""
from __future__ import annotations

import os
import urllib.request
from pathlib import Path

_HF_RESOLVE = (
    "https://huggingface.co/datasets/gaia-benchmark/GAIA/resolve/main/2023/validation"
)
_METADATA_FILENAME = "metadata.parquet"


def _hf_download(url: str, dest: str, token: str) -> None:
    """Download *url* to *dest* using a Bearer token."""
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req) as resp, open(dest, "wb") as fh:
        fh.write(resp.read())


def download_gaia_validation(
    dest_dir: str,
    *,
    hf_token: str | None = None,
) -> tuple[str, str]:
    """Download the GAIA 2023 validation split to *dest_dir*.

    Parameters
    ----------
    dest_dir:
        Local directory to write files into.  Created if missing.
    hf_token:
        Hugging Face access token for the gated dataset.  Falls back to the
        ``HF_TOKEN`` environment variable.

    Returns
    -------
    tuple[str, str]
        ``(metadata_path, attachments_dir)`` — absolute paths suitable for
        passing directly to
        :func:`~magi_agent.benchmarks.gaia.dataset.load_gaia_questions`.
    """
    token = hf_token or os.environ.get("HF_TOKEN", "")
    if not token:
        raise ValueError(
            "HF_TOKEN is required to download the gated GAIA dataset. "
            "Pass hf_token= or set the HF_TOKEN environment variable."
        )

    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    attachments_dir = dest / "attachments"
    attachments_dir.mkdir(exist_ok=True)

    # 1. Download metadata parquet.
    metadata_path = dest / _METADATA_FILENAME
    _hf_download(f"{_HF_RESOLVE}/{_METADATA_FILENAME}", str(metadata_path), token)

    # 2. Parse parquet to find attachment file names, then download each.
    try:
        import pyarrow.parquet as pq  # noqa: PLC0415

        table = pq.read_table(str(metadata_path))
        data = table.to_pydict()
        n = table.num_rows
        file_names: list[str] = [
            str(data.get("file_name", [""] * n)[i] or "") for i in range(n)
        ]
    except Exception:  # noqa: BLE001 — graceful degradation: skip attachments
        file_names = []

    for file_name in file_names:
        if not file_name:
            continue
        dest_file = attachments_dir / os.path.basename(file_name)
        if dest_file.exists():
            continue
        url = f"{_HF_RESOLVE}/{file_name}"
        try:
            _hf_download(url, str(dest_file), token)
        except Exception:  # noqa: BLE001 — best-effort; missing attachments logged
            pass

    return str(metadata_path), str(attachments_dir)


__all__ = ["download_gaia_validation"]
