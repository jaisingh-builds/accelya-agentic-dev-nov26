#!/usr/bin/env python3
"""Fallback for Lab 2: build and push the image with CodeBuild when local Docker isn't available.

Usage (from the repo root, after the Day 5 stack is deployed):
    python3 day5/scripts/build_in_cloud.py <your-id>
Zips day5/job (YOUR Dockerfile included), uploads it to s3://<bucket>/build/job.zip, runs the
accelya-m5-<id>-imagebuild project (same docker build --platform linux/amd64 as Lab 2), waits,
then prints the pushed image's tag and digest. Exit 0 = image accelya-m5-<id>-enrich:v1 is in ECR.
"""
import io
import sys
import time
import zipfile
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[2]
JOB = ROOT / "day5" / "job"


def main():
    if len(sys.argv) != 2:
        print(__doc__); return 2
    learner = sys.argv[1]
    if not (JOB / "Dockerfile").exists():
        print("day5/job/Dockerfile not found: write it first (Lab 2), check it with check_dockerfile.py"); return 2
    acct = boto3.client("sts").get_caller_identity()["Account"]
    bucket, project, repo = f"accelya-m5-{learner}-{acct}", f"accelya-m5-{learner}-imagebuild", f"accelya-m5-{learner}-enrich"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(JOB.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                z.write(p, p.relative_to(JOB).as_posix())
    boto3.client("s3").put_object(Bucket=bucket, Key="build/job.zip", Body=buf.getvalue())
    print(f"uploaded day5/job -> s3://{bucket}/build/job.zip ({len(buf.getvalue())} bytes)")

    cb = boto3.client("codebuild")
    build_id = cb.start_build(projectName=project)["build"]["id"]
    print(f"build started: {build_id}")
    last = None
    while True:
        b = cb.batch_get_builds(ids=[build_id])["builds"][0]
        if b["currentPhase"] != last:
            last = b["currentPhase"]; print(f"  {time.strftime('%H:%M:%S')}  {last}")
        if b["buildStatus"] != "IN_PROGRESS":
            break
        time.sleep(10)
    print(f"build {b['buildStatus']}")
    if b["buildStatus"] != "SUCCEEDED":
        g = b.get("logs", {})
        print(f"read the build log: aws logs tail {g.get('groupName')} --since 30m --format short")
        return 1
    img = boto3.client("ecr").describe_images(repositoryName=repo, imageIds=[{"imageTag": "v1"}])["imageDetails"][0]
    print(f"pushed {repo}:v1  {img['imageDigest']}  {img['imageSizeInBytes'] // 1_000_000} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
