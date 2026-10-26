# SPDX-License-Identifier: MIT OR Apache-2.0
"""Streaming CMS installation archives, with authentication before any tar parsing.

Online creation needs only a public recipient. PostgreSQL bytes are encrypted
before staging; decrypted dump/blobs exist only in the offline private quarantine.
The independently trusted outer digest is mandatory: encryption is not sender
identity. Neither archive creation nor successful decryption is a restore drill.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import stat
import subprocess
import tarfile
import tempfile
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Timer
from typing import IO, Annotated, Literal
from uuid import UUID, uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from offerloom.commands import canonical_json
from offerloom.storage import directory, subdirectory

MAX_BUNDLE_BYTES = 4 * 1024**3
MAX_DUMP_BYTES = 2 * 1024**3
MAX_MANIFEST_BYTES = 16 * 1024**2
MAX_FILES = 100_000
_CHUNK = 1024**2
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_COMPONENT = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}\Z")
# v1 is streaming BER ContentInfo carrying id-ct-authEnvelopedData. Pin this
# format discriminator; OpenSSL remains responsible for all ASN.1 and AEAD.
_AUTH_ENVELOPE = bytes.fromhex("3080060b2a864886f70d0109100117")
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class BackupError(ValueError):
    """Content-free failure code; child stderr and document bytes are never logged."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class BackupFile(_Model):
    path: Annotated[str, Field(min_length=1, max_length=240)]
    sha256: Digest
    byte_length: Annotated[int, Field(ge=1, le=MAX_BUNDLE_BYTES)]


class BackupManifest(_Model):
    format: Literal["offerloom-installation"]
    format_version: Literal[1]
    created_at: AwareDatetime
    source_namespace: UUID
    source_owner_id: UUID
    control_id: UUID
    installation_auth_epoch: Annotated[int, Field(ge=1)]
    owner_auth_epoch: Annotated[int, Field(ge=1)]
    maintenance_epoch: Annotated[int, Field(ge=1)]
    workspace_version: Annotated[int, Field(ge=1)]
    image_sha256: Digest
    source_sha256: Digest
    config_sha256: Digest
    recipient_sha256: Digest
    credential_keys: Annotated[dict[str, Digest], Field(min_length=1, max_length=100)]
    credential_active_key_id: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")]
    postgres_version: Annotated[str, Field(pattern=r"^18\.[0-9]+$")]
    dump_tool_version: Annotated[str, Field(min_length=1, max_length=200)]
    schema_versions: Annotated[dict[str, Digest], Field(min_length=1, max_length=1000)]
    table_counts: Annotated[dict[str, int], Field(min_length=1, max_length=1000)]
    table_sha256: Annotated[dict[str, Digest], Field(min_length=1, max_length=1000)]
    dump_sha256: Digest
    dump_byte_length: Annotated[int, Field(ge=1, le=MAX_DUMP_BYTES)]
    files: Annotated[list[BackupFile], Field(min_length=1, max_length=MAX_FILES)]


@dataclass(frozen=True)
class BundleReceipt:
    file_name: str
    sha256: str
    byte_length: int
    manifest_sha256: str


@dataclass(frozen=True)
class RecoveredBundle:
    manifest: BackupManifest
    dump: Path
    blobs: Path


def _components(value: str) -> tuple[str, ...]:
    parts = tuple(value.split("/"))
    if not 1 <= len(parts) <= 6 or any(
        part in (".", "..") or _COMPONENT.fullmatch(part) is None for part in parts
    ):
        raise BackupError("invalid_backup_member")
    return parts


@contextmanager
def _source(root: int, relative: str) -> Iterator[IO[bytes]]:
    parts = _components(relative)
    with ExitStack() as stack:
        parent = root
        for part in parts[:-1]:
            parent = stack.enter_context(subdirectory(parent, part))
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise BackupError("invalid_backup_file_type")
            yield stream


def _copy(source: IO[bytes], target: IO[bytes] | None, maximum: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    while chunk := source.read(min(_CHUNK, maximum - total + 1)):
        total += len(chunk)
        if total > maximum:
            raise BackupError("backup_size_limit")
        digest.update(chunk)
        if target is not None:
            target.write(chunk)
    return digest.hexdigest(), total


class _HashingReader(io.RawIOBase):
    def __init__(self, source: IO[bytes]) -> None:
        super().__init__()
        self.source = source
        self.digest = hashlib.sha256()
        self.byte_length = 0

    def read(self, size: int = -1) -> bytes:
        value = self.source.read(size)
        self.digest.update(value)
        self.byte_length += len(value)
        return value


def _private_file(path: Path) -> IO[bytes]:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    return os.fdopen(fd, "wb")


@contextmanager
def _child(
    command: list[str],
    *,
    stdin: int | IO[bytes],
    stdout: int | IO[bytes],
    pass_fds: tuple[int, ...] = (),
) -> Iterator[subprocess.Popen[bytes]]:
    process = subprocess.Popen(
        command, stdin=stdin, stdout=stdout, stderr=subprocess.DEVNULL, pass_fds=pass_fds
    )
    deadline = Timer(300, process.kill)
    deadline.daemon = True
    deadline.start()
    try:
        yield process
        if process.wait() != 0:
            raise BackupError("backup_crypto_failed")
    finally:
        deadline.cancel()
        deadline.join()
        if process.poll() is None:
            process.kill()
        process.wait()


@contextmanager
def _private_staging(root: Path) -> Iterator[Path]:
    with directory(root, create=True) as fd:
        if stat.S_IMODE(os.fstat(fd).st_mode) & 0o077:
            raise BackupError("backup_staging_not_private")
        # Keep the checked parent inode pinned while creating/removing quarantine.
        with tempfile.TemporaryDirectory(prefix=".backup-", dir=f"/dev/fd/{fd}") as value:
            yield Path(value)


@contextmanager
def recipient_certificate(path: Path) -> Iterator[tuple[int, str]]:
    with directory(path.parent) as parent, _source(parent, path.name) as stream:
        payload = stream.read(65537)
        if len(payload) > 65536:
            raise BackupError("invalid_backup_recipient")
        try:
            certificate = x509.load_pem_x509_certificate(payload)
            key = certificate.public_key()
            now = datetime.now(UTC)
            if (
                not isinstance(key, rsa.RSAPublicKey)
                or key.key_size < 3072
                or not certificate.not_valid_before_utc <= now < certificate.not_valid_after_utc
            ):
                raise BackupError("invalid_backup_recipient")
        except ValueError as error:
            raise BackupError("invalid_backup_recipient") from error
        stream.seek(0)
        yield stream.fileno(), certificate.fingerprint(hashes.SHA256()).hex()


def _encrypt(
    producer: Callable[[IO[bytes]], None],
    output: IO[bytes],
    recipient: int,
    execution_fds: tuple[int, ...] = (),
) -> None:
    with _child(
        [
            "openssl",
            "cms",
            "-encrypt",
            "-binary",
            "-aes-256-gcm",
            "-stream",
            "-outform",
            "DER",
            "-recip",
            f"/dev/fd/{recipient}",
            "-keyopt",
            "rsa_padding_mode:oaep",
            "-keyopt",
            "rsa_oaep_md:sha256",
        ],
        stdin=subprocess.PIPE,
        stdout=output,
        pass_fds=(recipient, *execution_fds),
    ) as process:
        assert process.stdin is not None
        with process.stdin as pipe:
            producer(pipe)
    output.flush()
    os.fsync(output.fileno())


def _add(
    archive: tarfile.TarFile, name: str, stream: IO[bytes] | _HashingReader, size: int
) -> None:
    member = tarfile.TarInfo(name)
    member.size = size
    member.mode = 0o600
    archive.addfile(member, stream)


def _validate(manifest: BackupManifest) -> None:
    names = [entry.path for entry in manifest.files]
    if names[0] != "database.cms" or len(set(names)) != len(names):
        raise BackupError("invalid_backup_members")
    for entry in manifest.files[1:]:
        _components(entry.path)
        if not entry.path.startswith("blobs/"):
            raise BackupError("invalid_backup_member")
    if sum(entry.byte_length for entry in manifest.files) > MAX_BUNDLE_BYTES - MAX_MANIFEST_BYTES:
        raise BackupError("backup_size_limit")
    if any(
        not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", name) or type(count) is not int or count < 0
        for name, count in manifest.table_counts.items()
    ) or any(not key.isdecimal() or int(key) < 1 for key in manifest.schema_versions):
        raise BackupError("invalid_backup_database_manifest")
    if manifest.table_counts.keys() != manifest.table_sha256.keys():
        raise BackupError("backup_table_manifest_mismatch")
    if manifest.credential_active_key_id not in manifest.credential_keys:
        raise BackupError("backup_active_credential_key_missing")


def create_bundle(
    *,
    dump: IO[bytes],
    blob_root: Path,
    blob_files: list[BackupFile],
    recipient: Path,
    staging_root: Path,
    destination: Path,
    manifest_factory: Callable[[str, int, str, list[BackupFile]], BackupManifest],
    execution_fds: tuple[int, ...] = (),
) -> BundleReceipt:
    """Caller must hold a live quiescent installation fence throughout this call.

    The manifest factory receives plaintext dump digest/size, recipient fingerprint
    and encrypted member inventory; it must supply the independently captured cut.
    Only ciphertext is staged online. No archive is published on partial failure.
    """
    paths = [entry.path for entry in blob_files]
    if len(paths) > MAX_FILES - 1 or len(set(paths)) != len(paths):
        raise BackupError("invalid_backup_blob_set")
    name = f"{uuid4()}.cms"
    temporary = f".pending-{uuid4()}"
    with (
        _private_staging(staging_root) as staging,
        recipient_certificate(recipient) as (certificate, fingerprint),
        directory(blob_root) as root,
        directory(destination, create=True) as output_root,
    ):
        inner = staging / "database.cms"
        dump_identity: list[tuple[str, int]] = []

        def encrypt_dump(pipe: IO[bytes]) -> None:
            dump_identity.append(_copy(dump, pipe, MAX_DUMP_BYTES))

        with _private_file(inner) as output:
            _encrypt(encrypt_dump, output, certificate, execution_fds)
        with inner.open("rb") as encrypted_dump:
            sha, size = _copy(encrypted_dump, None, MAX_DUMP_BYTES + _CHUNK)
        files = [BackupFile(path="database.cms", sha256=sha, byte_length=size), *blob_files]
        manifest = manifest_factory(*dump_identity[0], fingerprint, files)
        _validate(manifest)
        if (
            manifest.files != files
            or manifest.recipient_sha256 != fingerprint
            or (manifest.dump_sha256, manifest.dump_byte_length) != dump_identity[0]
        ):
            raise BackupError("backup_manifest_cut_mismatch")
        encoded = canonical_json(manifest.model_dump(mode="json")).encode()
        if len(encoded) > MAX_MANIFEST_BYTES:
            raise BackupError("backup_manifest_size_limit")

        def package(pipe: IO[bytes]) -> None:
            with tarfile.open(fileobj=pipe, mode="w|", format=tarfile.USTAR_FORMAT) as archive:
                _add(archive, "manifest.json", io.BytesIO(encoded), len(encoded))
                for entry in files:
                    with (
                        (
                            inner.open("rb")
                            if entry.path == "database.cms"
                            else _source(root, entry.path.removeprefix("blobs/"))
                        ) as source,
                        _HashingReader(source) as checked,
                    ):
                        _add(archive, entry.path, checked, entry.byte_length)
                        extra = checked.read(1)
                        if extra or (checked.digest.hexdigest(), checked.byte_length) != (
                            entry.sha256,
                            entry.byte_length,
                        ):
                            raise BackupError("backup_blob_changed")

        fd = os.open(
            temporary,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=output_root,
        )
        try:
            with os.fdopen(fd, "w+b") as output:
                _encrypt(package, output, certificate, execution_fds)
                output.seek(0)
                digest, size = _copy(output, None, MAX_BUNDLE_BYTES)
            # Do not replace an existing generation, even if a destination is hostile.
            os.link(
                temporary,
                name,
                src_dir_fd=output_root,
                dst_dir_fd=output_root,
                follow_symlinks=False,
            )
            os.fsync(output_root)
        finally:
            os.unlink(temporary, dir_fd=output_root)
            os.fsync(output_root)
    return BundleReceipt(name, digest, size, hashlib.sha256(encoded).hexdigest())


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise BackupError("duplicate_backup_manifest_key")
        result[key] = value
    return result


def _read_manifest(stream: IO[bytes]) -> BackupManifest:
    encoded = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(encoded) > MAX_MANIFEST_BYTES:
        raise BackupError("backup_manifest_size_limit")
    try:
        json.loads(encoded, object_pairs_hook=_unique)
        manifest = BackupManifest.model_validate_json(encoded, strict=True)
    except (ValidationError, ValueError, UnicodeError) as error:
        raise BackupError("invalid_backup_manifest") from error
    _validate(manifest)
    return manifest


def _decrypt(
    source: IO[bytes],
    output: IO[bytes],
    private_key: int,
    execution_fds: tuple[int, ...] = (),
) -> None:
    # Do not advance a BufferedReader: its read-ahead offset would be inherited
    # by OpenSSL's stdin even after a buffer-local seek back to zero.
    if os.pread(source.fileno(), len(_AUTH_ENVELOPE), 0) != _AUTH_ENVELOPE:
        raise BackupError("backup_crypto_format_forbidden")
    with _child(
        [
            "openssl",
            "cms",
            "-decrypt",
            "-binary",
            "-inform",
            "DER",
            "-inkey",
            f"/dev/fd/{private_key}",
        ],
        stdin=source,
        stdout=output,
        pass_fds=(private_key, *execution_fds),
    ):
        pass
    output.flush()
    os.fsync(output.fileno())


@contextmanager
def recover_bundle(
    *,
    source: Path,
    trusted_sha256: str,
    private_key: Path,
    staging_root: Path,
    expected_schema: dict[str, str],
    expected_image_sha256: str,
    execution_fds: tuple[int, ...] = (),
) -> Iterator[RecoveredBundle]:
    """Authenticate completely before parsing; never extract onto a live target.

    The yielded dump and blobs are valid only inside this private context. Native
    restore, independent DB/blob/key checks and revocation are still required.
    """
    if _DIGEST.fullmatch(trusted_sha256) is None:
        raise BackupError("trusted_backup_digest_required")
    with (
        _private_staging(staging_root) as quarantine,
        directory(source.parent) as source_root,
        _source(source_root, source.name) as incoming,
        directory(private_key.parent) as key_root,
        _source(key_root, private_key.name) as key,
    ):
        if stat.S_IMODE(os.fstat(key.fileno()).st_mode) & 0o077:
            raise BackupError("backup_private_key_not_private")
        cipher = quarantine / "input.cms"
        with _private_file(cipher) as output:
            digest, _ = _copy(incoming, output, MAX_BUNDLE_BYTES)
        if digest != trusted_sha256:
            raise BackupError("backup_ciphertext_hash_mismatch")
        plaintext = quarantine / "authenticated.tar"
        with cipher.open("rb") as encoded, _private_file(plaintext) as output:
            _decrypt(encoded, output, key.fileno(), execution_fds)
        if plaintext.stat().st_size > MAX_BUNDLE_BYTES:
            raise BackupError("backup_size_limit")
        offset = 0
        with plaintext.open("rb") as decoded, tarfile.open(fileobj=decoded, mode="r|") as archive:
            first = archive.next()
            if (
                first is None
                or first.name != "manifest.json"
                or first.type != tarfile.REGTYPE
                or first.size > MAX_MANIFEST_BYTES
                or first.offset != 0
                or first.offset_data != 512
                or first.linkname
                or first.pax_headers
            ):
                raise BackupError("invalid_backup_manifest_member")
            member_stream = archive.extractfile(first)
            assert member_stream is not None
            with member_stream:
                manifest = _read_manifest(member_stream)
            if manifest.schema_versions != expected_schema:
                raise BackupError("incompatible_backup_schema")
            if manifest.image_sha256 != expected_image_sha256:
                raise BackupError("incompatible_backup_image")
            offset = 512 + ((first.size + 511) // 512) * 512
            for entry in manifest.files:
                member = archive.next()
                if (
                    member is None
                    or member.name != entry.path
                    or member.type != tarfile.REGTYPE
                    or member.linkname
                    or member.pax_headers
                    or member.size != entry.byte_length
                    or member.offset != offset
                    or member.offset_data != offset + 512
                ):
                    raise BackupError("backup_member_set_mismatch")
                target = quarantine.joinpath(*_components(entry.path))
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                extracted = archive.extractfile(member)
                assert extracted is not None
                with extracted, _private_file(target) as output:
                    if _copy(extracted, output, entry.byte_length) != (
                        entry.sha256,
                        entry.byte_length,
                    ):
                        raise BackupError("backup_member_hash_mismatch")
                offset += 512 + ((entry.byte_length + 511) // 512) * 512
            if archive.next() is not None:
                raise BackupError("unexpected_backup_member")
        # tar readers ignore trailing bytes; this format permits only zero end padding.
        with plaintext.open("rb") as decoded:
            decoded.seek(offset)
            tail = decoded.read(tarfile.RECORDSIZE + 1025)
            if not 1024 <= len(tail) <= tarfile.RECORDSIZE + 1024 or any(tail):
                raise BackupError("invalid_backup_trailer")
        dump = quarantine / "database.dump"
        with (quarantine / "database.cms").open("rb") as encoded, _private_file(dump) as output:
            _decrypt(encoded, output, key.fileno(), execution_fds)
        with dump.open("rb") as restored:
            if _copy(restored, None, MAX_DUMP_BYTES) != (
                manifest.dump_sha256,
                manifest.dump_byte_length,
            ):
                raise BackupError("backup_dump_hash_mismatch")
        blobs = quarantine / "blobs"
        blobs.mkdir(mode=0o700, exist_ok=True)
        yield RecoveredBundle(manifest, dump, blobs)
