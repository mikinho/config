#!/usr/bin/env python3
"""Render the generic transaction core for one validated application profile."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Mapping, NoReturn, Sequence

from validate_profile import (
    ApplicationProfile,
    ProfileError,
    load_profile,
    read_bounded_regular_file,
)


APPLICATION_DIRECTORY: Final = Path(__file__).resolve().parent
TEMPLATE_DIRECTORY: Final = APPLICATION_DIRECTORY / "templates"
STANDARD_PATH: Final = APPLICATION_DIRECTORY / "standard-v1.md"
STANDARD_VERSION: Final = "1"
BUNDLE_SCHEMA_VERSION: Final = 1
MAX_TEMPLATE_BYTES: Final = 512 * 1024
MAX_STANDARD_BYTES: Final = 512 * 1024
SOURCE_REVISION_PATTERN: Final = re.compile(r"^[0-9a-f]{40,64}$")
PLACEHOLDER_PATTERN: Final = re.compile(r"@@[A-Z0-9_]+@@")
SELINUX_IDENTIFIER_PATTERN: Final = re.compile(r"^[a-z_][a-z0-9_]{0,126}$")
SCRIPT_MODE: Final = 0o755
DATA_MODE: Final = 0o644
CORE_TEMPLATES: Final = {
    "scripts/deploy-gateway.py": "deploy-gateway.py.in",
    "scripts/deploy-trigger.py": "deploy-trigger.py.in",
    "scripts/snapshot-manifests.py": "snapshot-manifests.py.in",
}


class RenderError(ValueError):
    """Raised when a core bundle cannot be rendered safely."""


@dataclass(frozen=True)
class RenderedFile:
    """One deterministic bundle payload and its installed mode."""

    relative_path: str
    content: bytes
    mode: int

    @property
    def digest(self) -> str:
        """Return the SHA-256 digest of this payload."""

        return hashlib.sha256(self.content).hexdigest()


def fail(message: str) -> NoReturn:
    """Raise one deterministic renderer failure."""

    raise RenderError(message)


def profile_section(profile: ApplicationProfile, name: str) -> Mapping[str, object]:
    """Return one already-validated object from the canonical profile."""

    value = profile.source[name]
    if not isinstance(value, dict):
        fail(f"validated profile section changed type: {name}")
    return value


def string_value(section: Mapping[str, object], name: str) -> str:
    """Return one already-validated string member."""

    value = section[name]
    if not isinstance(value, str):
        fail(f"validated profile field changed type: {name}")
    return value


def environment_prefix(tag: str) -> str:
    """Return a shell-safe uppercase prefix derived from an application tag."""

    return tag.upper().replace("-", "_")


def trigger_selinux_type(deploy_user: str) -> str:
    """Return the project-specific SELinux trigger type."""

    value = f"{deploy_user.replace('-', '_')}_trigger_t"
    if SELINUX_IDENTIFIER_PATTERN.fullmatch(value) is None:
        fail("derived SELinux trigger type is invalid")
    return value


def build_replacements(profile: ApplicationProfile) -> dict[str, str]:
    """Build the complete fixed placeholder map for generic core templates."""

    paths = profile_section(profile, "paths")
    transport = profile_section(profile, "transport")
    identity = profile.identity
    prefix = environment_prefix(identity.tag)
    application_root = string_value(paths, "applicationRoot")
    dependency_state_root = string_value(paths, "dependencyStateRoot")
    return {
        "@@DEPLOY_USER@@": identity.deploy_user,
        "@@DEPLOY_GROUP@@": identity.deploy_group,
        "@@SERVICE_GROUP@@": identity.service_group,
        "@@TRIGGER_ROOT@@": string_value(paths, "triggerRoot"),
        "@@INCOMING_ROOT@@": f"{application_root}/incoming",
        "@@SNAPSHOT_WORK_ROOT@@": f"{dependency_state_root}/npm-work",
        "@@SESSION_COMMAND@@": string_value(transport, "sessionCommand"),
        "@@GATEWAY_TEST_ENV@@": f"{prefix}_DEPLOY_GATEWAY_TESTING",
        "@@TRIGGER_TEST_ENV@@": f"{prefix}_DEPLOY_TRIGGER_TESTING",
        "@@TRIGGER_STOP_ENV@@": f"{prefix}_DEPLOY_TRIGGER_STOP_AFTER_BLOCKER",
        "@@SNAPSHOT_TEST_ENV@@": f"{prefix}_MANIFEST_SNAPSHOT_TESTING",
        "@@TRIGGER_SELINUX_TYPE@@": trigger_selinux_type(identity.deploy_user),
    }


def read_template(name: str) -> str:
    """Read one trusted template as strict UTF-8."""

    path = TEMPLATE_DIRECTORY / name
    try:
        content = read_bounded_regular_file(path, MAX_TEMPLATE_BYTES, "template")
        return content.decode("utf-8")
    except UnicodeDecodeError as error:
        fail(f"template is not valid UTF-8: {path}: {error}")


def render_template(name: str, replacements: Mapping[str, str]) -> bytes:
    """Render one template and reject unknown or unused placeholders."""

    content = read_template(name)
    found = frozenset(PLACEHOLDER_PATTERN.findall(content))
    unknown = sorted(found - replacements.keys())
    if unknown:
        fail(f"template {name} contains unknown placeholders: {', '.join(unknown)}")
    for placeholder in found:
        content = content.replace(placeholder, replacements[placeholder])
    remaining = sorted(set(PLACEHOLDER_PATTERN.findall(content)))
    if remaining:
        fail(f"template {name} retains placeholders: {', '.join(remaining)}")
    if not content.endswith("\n"):
        fail(f"template {name} must end with a newline")
    return content.encode("utf-8")


def canonical_json(value: object) -> bytes:
    """Encode deterministic, human-reviewable JSON with one trailing newline."""

    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def build_payload(
    profile: ApplicationProfile, source_revision: str
) -> tuple[RenderedFile, ...]:
    """Render all core scripts, profile provenance, and the bundle manifest."""

    if SOURCE_REVISION_PATTERN.fullmatch(source_revision) is None:
        fail("source revision must contain 40 to 64 lowercase hexadecimal characters")
    replacements = build_replacements(profile)
    files = [
        RenderedFile(path, render_template(template, replacements), SCRIPT_MODE)
        for path, template in sorted(CORE_TEMPLATES.items())
    ]
    files.append(RenderedFile("profile.json", profile.canonical_bytes(), DATA_MODE))

    standard_bytes = read_bounded_regular_file(
        STANDARD_PATH, MAX_STANDARD_BYTES, "standard"
    )
    payload_manifest = [
        {
            "mode": f"{rendered.mode:04o}",
            "path": rendered.relative_path,
            "sha256": rendered.digest,
        }
        for rendered in sorted(files, key=lambda item: item.relative_path)
    ]
    manifest = {
        "bundleKind": "application-deployment-core",
        "bundleSchemaVersion": BUNDLE_SCHEMA_VERSION,
        "conformanceStatus": "core-only",
        "files": payload_manifest,
        "profileSha256": profile.digest(),
        "sourceRevision": source_revision,
        "standardSha256": hashlib.sha256(standard_bytes).hexdigest(),
        "standardVersion": STANDARD_VERSION,
    }
    files.append(
        RenderedFile("STANDARD-MANIFEST.json", canonical_json(manifest), DATA_MODE)
    )
    checksum_lines = [
        f"{rendered.digest}  {rendered.relative_path}\n"
        for rendered in sorted(files, key=lambda item: item.relative_path)
    ]
    files.append(RenderedFile("SHA256SUMS", "".join(checksum_lines).encode(), DATA_MODE))
    return tuple(sorted(files, key=lambda item: item.relative_path))


def write_rendered_file(root: Path, rendered: RenderedFile) -> None:
    """Create one bundle payload without following a pre-existing entry."""

    destination = root / rendered.relative_path
    destination.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    try:
        with destination.open("xb") as stream:
            stream.write(rendered.content)
            stream.flush()
            os.fsync(stream.fileno())
        destination.chmod(rendered.mode)
    except OSError as error:
        fail(f"cannot write rendered file {rendered.relative_path}: {error}")


def render_bundle(profile: ApplicationProfile, output: Path, source_revision: str) -> None:
    """Publish one deterministic core bundle to a new output directory."""

    output = output.absolute()
    parent = output.parent.resolve(strict=True)
    if not parent.is_dir() or parent.is_symlink():
        fail(f"output parent must be a real directory: {parent}")
    if output.exists() or output.is_symlink():
        fail(f"output must not already exist: {output}")
    payload = build_payload(profile, source_revision)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=parent))
    staging.chmod(0o700)
    published = False
    try:
        for rendered in payload:
            write_rendered_file(staging, rendered)
        directory_descriptor = os.open(staging, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
        if output.exists() or output.is_symlink():
            fail(f"output appeared during render: {output}")
        staging.rename(output)
        published = True
    finally:
        if not published:
            shutil.rmtree(staging, ignore_errors=True)


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""

    parser = argparse.ArgumentParser(
        description="Render the generic gold deployment transaction core."
    )
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-revision", required=True)
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    """Run the deterministic core renderer."""

    options = build_parser().parse_args(arguments)
    try:
        profile = load_profile(options.profile)
        render_bundle(profile, options.output, options.source_revision)
    except (OSError, ProfileError, RenderError) as error:
        print(f"render-core: ERROR: {error}", file=sys.stderr)
        return 1
    print(f"Rendered application deployment core: {options.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
