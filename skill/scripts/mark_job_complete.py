#!/usr/bin/env python3
"""Record a generated image as a completed job in imagegen-jobs.json.

Replaces the shell + jq manifest-update steps so the workflow runs on Windows
without jq or bash. Copies the selected generation output into the job's
decoded path, marks the job complete, and for the base job also refreshes
references/canonical-base.png.
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


def load_jobs(path: Path) -> dict:
    if not path.is_file():
        raise SystemExit(f"job manifest not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--source", required=True, help="selected generated image")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).expanduser().resolve()
    source = Path(args.source).expanduser().resolve()
    if not source.is_file():
        raise SystemExit(f"selected source image not found: {source}")

    manifest_path = run_dir / "imagegen-jobs.json"
    manifest = load_jobs(manifest_path)
    jobs = manifest.get("jobs")
    if not isinstance(jobs, list):
        raise SystemExit(f"malformed job manifest: {manifest_path}")

    job = next((j for j in jobs if j.get("id") == args.job_id), None)
    if job is None:
        known = ", ".join(str(j.get("id")) for j in jobs)
        raise SystemExit(f"unknown job id {args.job_id!r}; known ids: {known}")

    output_rel = job.get("output_path")
    if not output_rel:
        raise SystemExit(f"job {args.job_id!r} has no output_path in the manifest")

    output_path = run_dir / output_rel
    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, output_path)
    print(f"copied {source} -> {output_path}")

    job["status"] = "complete"
    job["source_path"] = str(source)
    job["completed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"marked job {args.job_id!r} complete")

    if args.job_id == "base":
        canonical = run_dir / "references" / "canonical-base.png"
        canonical.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(output_path, canonical)
        print(f"wrote canonical base reference: {canonical}")


if __name__ == "__main__":
    main()
