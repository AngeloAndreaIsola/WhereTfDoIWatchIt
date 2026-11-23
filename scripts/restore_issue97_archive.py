"""Verify the sealed local #97 archive and restore its allowlisted files offline."""
import argparse
import hashlib
import json
import os
import tarfile
from pathlib import Path

DOCS = Path(__file__).resolve().parents[1] / "docs/audits/stage2b"


def restore(destination, archive_path=None):
    archive = json.loads((DOCS / "issue97-evidence-manifest.json").read_text())["private_archive"]
    path = archive_path or Path.home() / archive["location_relative_to_owner_home"]
    expected = archive["members_sha256"]
    with path.open("rb") as source:
        if hashlib.sha256(source.read()).hexdigest() != archive["sha256"]:
            raise ValueError("Archive SHA-256 mismatch")
        source.seek(0)
        with tarfile.open(fileobj=source, mode="r:") as bundle:
            members = bundle.getmembers()
            if sorted(m.name for m in members) != sorted(expected):
                raise ValueError("Archive membership differs from manifest")
            destination.mkdir(mode=0o700, exist_ok=False)
            for member in members:
                name = Path(member.name)
                if not member.isfile() or name.is_absolute() or ".." in name.parts:
                    raise ValueError(f"Unexpected archive entry: {name}")
                with bundle.extractfile(member) as handle:
                    data = handle.read()
                if hashlib.sha256(data).hexdigest() != expected[member.name]:
                    raise ValueError(f"Changed archive member: {name}")
                target = destination / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as handle:
                    handle.write(data)
                target.chmod(0o400)
                if hashlib.sha256(target.read_bytes()).hexdigest() != expected[member.name]:
                    raise ValueError(f"Restored SHA-256 mismatch: {name}")
    print(f"Verified the archive and {len(expected)} restored file digests.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, help="Defaults to the owner-local sealed copy")
    parser.add_argument("--restore-to", required=True, type=Path, help="New directory under a private parent")
    args = parser.parse_args()
    os.umask(0o077)
    restore(args.restore_to, args.archive)
