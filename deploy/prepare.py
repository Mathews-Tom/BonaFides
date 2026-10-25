# SPDX-License-Identifier: MIT OR Apache-2.0
"""One-shot local volume ownership and private secret materialization, not an app role."""

import os
import shutil
from pathlib import Path


def main() -> None:
    mappings = {
        "/state/app-secrets": (10001, ("runtime_dsn", "keyring", "bootstrap_token")),
        "/state/migration-secrets": (10001, ("migrator_dsn",)),
        "/state/db-secrets": (70, ("admin_password", "migrator_password", "runtime_password")),
    }
    for directory, (uid, names) in mappings.items():
        root = Path(directory)
        # Normalize ownership before chmod; the helper deliberately lacks CAP_FOWNER.
        os.chown(root, 0, 0)
        root.chmod(0o700)
        os.chown(root, uid, uid)
        for name in names:
            source = Path("/input") / name
            if source.is_symlink() or not source.is_file() or source.stat().st_mode & 0o077:
                raise SystemExit("secret source must be a private regular file")
            target = root / name
            temporary = root / (name + ".new")
            with temporary.open("wb") as output, source.open("rb") as incoming:
                temporary.chmod(0o600)
                shutil.copyfileobj(incoming, output)
            temporary.chmod(0o400)
            os.chown(temporary, uid, uid)
            temporary.replace(target)
    for directory, uid in (
        ("/state/pg", 70),
        ("/state/blobs", 10001),
        ("/state/spool", 10001),
        ("/state/import-spool", 10001),
    ):
        path = Path(directory)
        os.chown(path, 0, 0)
        path.chmod(0o700)
        os.chown(path, uid, uid)
    print("private volumes prepared")


if __name__ == "__main__":
    main()
