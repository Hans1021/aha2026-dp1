"""reference helpers for the SPI notebook workflow."""

# import only the dependencies used by this module.
from pathlib import Path
from pathlib import PurePosixPath
import hashlib
import io
import json
import os
import subprocess
import tarfile


# the accepted reference remains fixed even if its tag is moved.
REFERENCE = "baselines/recovered-v1"


REFERENCE_TAG = "baseline-recovered-v1"


REFERENCE_COMMIT = "4856f248d82857a613a6ff5875d65f200dd29799"


# prevent inherited Git overrides from redirecting reference lookup.
def _environment():
    environment = dict(os.environ)
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                 "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                 "PYTHONPATH", "PYTHONHOME"):
        environment.pop(name, None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


# read Git objects without checkout, index, branch, or worktree changes.
def _git(root, *arguments, timeout=10):
    completed = subprocess.run(["git", *arguments], cwd=root, env=_environment(),
                               capture_output=True, check=False, timeout=timeout)
    if completed.returncode:
        raise RuntimeError("Cannot read frozen baseline: "
                           + completed.stderr.decode("utf-8", errors="replace").strip())
    return completed.stdout


# retain the historical manifest and evidence byte-for-byte in this checkout.
def verify_tagged_reference(root):
    root = Path(root).resolve()
    commit = _git(root, "rev-parse", "--verify", REFERENCE_TAG + "^{commit}").decode().strip()
    if commit != REFERENCE_COMMIT:
        raise ValueError("Frozen baseline tag no longer identifies the accepted commit")
    original = _git(root, "show", f"{REFERENCE_COMMIT}:{REFERENCE}/manifest.json")
    reference = root / REFERENCE
    if (reference / "manifest.json").read_bytes() != original:
        raise ValueError("Working baseline manifest differs from the frozen Git commit")
    manifest = json.loads(original)
    # only fixed, in-directory evidence paths may be checked.
    for name, expected in manifest["evidence"].items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Invalid frozen evidence path")
        path = reference.joinpath(*relative.parts).resolve()
        if not path.is_relative_to(reference.resolve()):
            raise ValueError("Frozen evidence escapes its reference directory")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"Frozen baseline evidence changed: {name}")
    return manifest


# extract regular historical files into a new directory and verify their hashes.
def materialize_reference(root, destination):
    root, destination = Path(root).resolve(), Path(destination).resolve()
    manifest = verify_tagged_reference(root)
    payload = _git(root, "archive", "--format=tar", REFERENCE_COMMIT, timeout=30)
    # reject traversal, links, and special files before writing any archive member.
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        members = archive.getmembers()
        seen = set()
        for member in members:
            relative = PurePosixPath(member.name)
            if (relative.is_absolute() or ".." in relative.parts or not relative.parts
                    or member.name in seen or not (member.isdir() or member.isfile())):
                raise ValueError(f"Unsafe baseline archive member: {member.name}")
            seen.add(member.name)
        destination.mkdir(parents=True, exist_ok=False)
        # manual extraction never follows links supplied by an archive.
        for member in members:
            path = destination.joinpath(*PurePosixPath(member.name).parts)
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source:
                    path.write_bytes(source.read())
                path.chmod(member.mode & 0o777)
    # verify original sources independently of the rewritten notebook workflow.
    for name, expected in manifest["sources"].items():
        path = destination / name
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"Archived baseline source changed: {name}")
    for name, expected in manifest["evidence"].items():
        path = destination / REFERENCE / name
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"Archived baseline evidence changed: {name}")
    return destination
