# SPDX-License-Identifier: MIT OR Apache-2.0
"""One-shot local volume ownership and private secret materialization, not an app role."""

import os
import shutil
import stat
from pathlib import Path


def main() -> None:
    mappings = {
        "/state/app-secrets": (
            10001,
            ("runtime_dsn", "keyring", "bootstrap_token", "local_model_route.json"),
        ),
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
            target = root / name
            temporary = root / (name + ".new")
            try:
                descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            except FileNotFoundError:
                if name != "local_model_route.json":
                    raise SystemExit("required secret source is missing") from None
                # Revocation uses open(), not metadata that a host mount can cache.
                target.unlink(missing_ok=True)
                temporary.unlink(missing_ok=True)
                continue
            except OSError:
                raise SystemExit("secret source cannot be opened safely") from None
            with os.fdopen(descriptor, "rb") as incoming:
                info = os.fstat(incoming.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                    raise SystemExit("secret source must be a private regular file")
                if name == "local_model_route.json" and info.st_size > 65536:
                    raise SystemExit("local model route source exceeds the configuration limit")
                with temporary.open("wb") as output:
                    temporary.chmod(0o600)
                    if name == "local_model_route.json":
                        payload = incoming.read(65537)
                        if len(payload) > 65536:
                            raise SystemExit(
                                "local model route source exceeds the configuration limit"
                            )
                        output.write(payload)
                    else:
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
