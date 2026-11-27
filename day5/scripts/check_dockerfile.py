#!/usr/bin/env python3
"""Check day5/job/Dockerfile against the course container standards (Module 5).

Usage (from the repo root):  python3 day5/scripts/check_dockerfile.py [path/to/Dockerfile]
Exit 0 = all checks pass. Each failure says what to fix.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "day5" / "job" / "Dockerfile"


def instructions(text):
    """(KEYWORD, args) per instruction, comments and line continuations handled."""
    out, buf = [], ""
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        buf += (" " if buf else "") + s.rstrip("\\").strip()
        if s.endswith("\\"):
            continue
        kw, _, args = buf.partition(" ")
        out.append((kw.upper(), args.strip()))
        buf = ""
    return out


def main():
    if not path.exists():
        print(f"  ✗ {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path} not found: write it in Lab 2")
        print("\nDockerfile: 0/8 checks pass")
        return 1
    ins = instructions(path.read_text())
    kws = [k for k, _ in ins]
    frm = [a for k, a in ins if k == "FROM"]
    base = frm[-1].split()[0] if frm else ""
    copies = [(i, a) for i, (k, a) in enumerate(ins) if k == "COPY"]
    runs = [a for k, a in ins if k == "RUN"]
    users = [a for k, a in ins if k == "USER"]
    entry = [a for k, a in ins if k in ("ENTRYPOINT", "CMD")]
    envs = " ".join(a for k, a in ins if k in ("ENV", "ARG"))

    def idx(name):
        return next((i for i, a in copies if name in a.split()[0:-1] or a.split()[0] == name), None)

    pinned = bool(re.search(r"@sha256:[0-9a-f]{64}$", base)) or bool(re.search(r":\d+\.\d+\.\d+[-\w.]*$", base))
    try:
        exec_form = bool(entry) and isinstance(json.loads(entry[-1]), list)
    except ValueError:
        exec_form = False
    req_i, code_i = idx("requirements.txt"), idx("enrich.py")
    checks = [
        ("base image pinned to an exact version (x.y.z) or digest, never 'latest'", pinned,
         f"FROM {base or '<missing>'}: use e.g. python:3.12.12-slim"),
        ("base image from ECR Public (public.ecr.aws), not Docker Hub", base.startswith("public.ecr.aws/"),
         "Docker Hub rate-limits shared build IPs: public.ecr.aws/docker/library/python:<version>-slim"),
        ("a slim base image", "slim" in base or "alpine" in base or "distroless" in base,
         "full images are ~1 GB: pick a -slim variant"),
        ("requirements.txt copied and installed before the code (layer cache)",
         req_i is not None and code_i is not None and req_i < code_i,
         "COPY requirements.txt, RUN pip install, THEN COPY enrich.py"),
        ("pip install --no-cache-dir", any("pip install" in r and "--no-cache-dir" in r for r in runs),
         "add --no-cache-dir so the wheel cache doesn't ship in the image"),
        ("no 'COPY . .': copy only what the job needs", not any(a.split()[0] in (".", "./") for _, a in copies),
         "COPY enrich.py and ref/ explicitly"),
        ("runs as a non-root USER", bool(users) and users[-1].split(":")[0] not in ("root", "0"),
         "add a user (useradd) and USER <name> before ENTRYPOINT"),
        ("ENTRYPOINT in exec form, no secrets in ENV/ARG",
         exec_form and not re.search(r"(?i)(secret|password|token|access_key|aws_key)", envs),
         'ENTRYPOINT ["python", "enrich.py"]; configuration comes from the task definition'),
    ]
    ok = 0
    for name, passed, fix in checks:
        ok += passed
        print(f"  {'✓' if passed else '✗'} {name}" + ("" if passed else f"\n      fix: {fix}"))
    print(f"\nDockerfile: {ok}/{len(checks)} checks pass")
    return 0 if ok == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
