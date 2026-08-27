#!/usr/bin/env python3

#
# Author: Michael Welter <me@mikinho.com> - https://github.com/mikinho
#

"""Render the generic transaction core for one validated application profile."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
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
    "scripts/package-hash.mjs": "package-hash.mjs.in",
    "scripts/post-deploy": "post-deploy.in",
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


@dataclass(frozen=True)
class TemplateSpec:
    """One trusted source template and its rendered bundle destination."""

    relative_path: str
    template_name: str
    mode: int


def fail(message: str) -> NoReturn:
    """Raise one deterministic renderer failure."""

    raise RenderError(message)


def environment_prefix(tag: str) -> str:
    """Return a shell-safe uppercase prefix derived from an application tag."""

    return tag.upper().replace("-", "_")


def trigger_selinux_type(deploy_user: str) -> str:
    """Return the project-specific SELinux trigger type."""

    value = f"{deploy_user.replace('-', '_')}_trigger_t"
    if SELINUX_IDENTIFIER_PATTERN.fullmatch(value) is None:
        fail("derived SELinux trigger type is invalid")
    return value


def shell_literal(value: str) -> str:
    """Return one POSIX-shell literal for a validated profile value."""

    return shlex.quote(value)


def shell_array(values: Sequence[str]) -> str:
    """Render one deterministic Bash array body from validated strings."""

    return "\n".join(f"    {shell_literal(value)}" for value in values)


def candidate_unit_prefix(candidate_service: str) -> str:
    """Return the fixed prefix of one validated template service unit."""

    suffix = "@.service"
    if not candidate_service.endswith(suffix):
        fail("candidate service does not use the required template-unit suffix")
    return candidate_service[: -len(suffix)]


def allowed_candidate_roots(profile: ApplicationProfile) -> tuple[str, ...]:
    """Return the closed set of permitted candidate top-level entries."""

    relative_paths = (
        profile.runtime.entrypoint,
        profile.metadata.path,
        profile.dependencies.provenance_path,
        *profile.dependencies.manifests,
        *profile.static_content.release_paths,
        "node_modules",
    )
    return tuple(sorted({value.split("/", maxsplit=1)[0] for value in relative_paths}))


def failure_service(profile: ApplicationProfile) -> str:
    """Return the derived failure-observer template service name."""

    return f"{profile.services.finalizer.removesuffix('.service')}-failure@.service"


def gateway_program(profile: ApplicationProfile) -> str:
    """Return the fixed root-owned forced-command gateway path."""

    program = f"{profile.identity.tag.replace('_', '-')}-deploy-gateway"
    return f"/usr/local/bin/{program}"


def network_policy(profile: ApplicationProfile) -> str:
    """Render the reviewed outbound and loopback policy for systemd units."""

    if profile.runtime.allow_loopback:
        return ""
    return (
        "IPAddressAllow=127.0.0.53/32\n"
        "IPAddressDeny=localhost"
    )


def selinux_static_patterns(profile: ApplicationProfile) -> tuple[str, ...]:
    """Return exact fcontext regexes for nginx traversal and static content."""

    root = re.escape(profile.paths.application_root)
    patterns = {
        root,
        f"{root}/releases",
        f"{root}/releases/[^/]+",
    }
    patterns.update(
        f"{root}/releases/[^/]+/{re.escape(path)}(/.*)?"
        for path in profile.static_content.release_paths
    )
    return tuple(sorted(patterns))


def selinux_pointer_patterns(profile: ApplicationProfile) -> tuple[str, ...]:
    """Return fcontext regexes kept on the ordinary systemd-traversable type."""

    root = re.escape(profile.paths.application_root)
    return (
        f"{root}/candidates(/.*)?",
        f"{root}/current",
        f"{root}/previous",
    )


def selinux_relabel_paths(profile: ApplicationProfile) -> tuple[str, ...]:
    """Return bounded application paths that may need label reconciliation."""

    root = profile.paths.application_root
    return (
        root,
        f"{root}/candidates",
        f"{root}/current",
        f"{root}/previous",
        f"{root}/releases",
    )


def template_specs(profile: ApplicationProfile) -> tuple[TemplateSpec, ...]:
    """Return all generic transaction and host-policy template outputs."""

    specs = [
        TemplateSpec(path, template, SCRIPT_MODE)
        for path, template in CORE_TEMPLATES.items()
    ]
    specs.extend(
        (
            TemplateSpec(
                f"systemd/{profile.services.live}", "live.service.in", DATA_MODE
            ),
            TemplateSpec(
                f"systemd/{profile.services.candidate}",
                "candidate@.service.in",
                DATA_MODE,
            ),
            TemplateSpec(
                f"systemd/{profile.services.finalizer}",
                "finalizer.service.in",
                DATA_MODE,
            ),
            TemplateSpec(
                f"systemd/{profile.services.path}", "finalizer.path.in", DATA_MODE
            ),
            TemplateSpec(
                f"systemd/{profile.services.recovery}",
                "recovery.service.in",
                DATA_MODE,
            ),
            TemplateSpec(
                f"systemd/{failure_service(profile)}", "failure@.service.in", DATA_MODE
            ),
            TemplateSpec("ssh/deploy.conf", "ssh-deploy.conf.in", DATA_MODE),
            TemplateSpec(
                "ssh/maintenance.conf", "ssh-maintenance.conf.in", DATA_MODE
            ),
            TemplateSpec("selinux/runtime.te", "runtime.te.in", DATA_MODE),
            TemplateSpec(
                "scripts/selinux-setup", "selinux-setup.in", SCRIPT_MODE
            ),
            TemplateSpec("scripts/verify-host", "verify-host.in", SCRIPT_MODE),
            TemplateSpec("setup", "setup.in", SCRIPT_MODE),
        )
    )
    return tuple(sorted(specs, key=lambda item: item.relative_path))


def systemd_units(profile: ApplicationProfile) -> tuple[str, ...]:
    """Return all rendered unit filenames in deterministic order."""

    return tuple(
        sorted(
            (
                profile.services.live,
                profile.services.candidate,
                profile.services.finalizer,
                profile.services.path,
                profile.services.recovery,
                failure_service(profile),
            )
        )
    )


def secure_scripts() -> tuple[str, ...]:
    """Return all root-owned runtime scripts installed from a bundle."""

    names = {
        Path(path).name for path in CORE_TEMPLATES if path.startswith("scripts/")
    }
    names.update(("selinux-setup", "verify-host"))
    return tuple(sorted(names))


def build_replacements(
    profile: ApplicationProfile, source_revision: str
) -> dict[str, str]:
    """Build the complete fixed placeholder map for generic core templates."""

    identity = profile.identity
    prefix = environment_prefix(identity.tag)
    candidate_prefix = candidate_unit_prefix(profile.services.candidate)
    health_retries = (profile.health.timeout_seconds + 1) // 2
    return {
        "@@DEPLOY_USER@@": identity.deploy_user,
        "@@DEPLOY_GROUP@@": identity.deploy_group,
        "@@SERVICE_GROUP@@": identity.service_group,
        "@@TRIGGER_ROOT@@": profile.paths.trigger_root,
        "@@INCOMING_ROOT@@": f"{profile.paths.application_root}/incoming",
        "@@SNAPSHOT_WORK_ROOT@@": f"{profile.paths.dependency_state_root}/npm-work",
        "@@SESSION_COMMAND@@": profile.transport.session_command,
        "@@GATEWAY_TEST_ENV@@": f"{prefix}_DEPLOY_GATEWAY_TESTING",
        "@@TRIGGER_TEST_ENV@@": f"{prefix}_DEPLOY_TRIGGER_TESTING",
        "@@TRIGGER_STOP_ENV@@": f"{prefix}_DEPLOY_TRIGGER_STOP_AFTER_BLOCKER",
        "@@SNAPSHOT_TEST_ENV@@": f"{prefix}_MANIFEST_SNAPSHOT_TESTING",
        "@@TRIGGER_SELINUX_TYPE@@": trigger_selinux_type(identity.deploy_user),
        "@@APPLICATION_ROOT@@": profile.paths.application_root,
        "@@SECURE_ROOT@@": profile.paths.secure_root,
        "@@DEPENDENCY_STATE_ROOT@@": profile.paths.dependency_state_root,
        "@@DEPENDENCY_CACHE_ROOT@@": profile.paths.dependency_cache_root,
        "@@SERVICE_USER@@": identity.service_user,
        "@@LIVE_SERVICE@@": profile.services.live,
        "@@RUNTIME_SOCKET@@": (
            f"{profile.paths.runtime_root}/{profile.runtime.socket_name}"
        ),
        "@@ENTRYPOINT@@": profile.runtime.entrypoint,
        "@@METADATA_PATH@@": profile.metadata.path,
        "@@METADATA_TOKEN_POINTER_LITERAL@@": shell_literal(
            profile.metadata.deployment_token_pointer
        ),
        "@@DEPENDENCY_PROVENANCE_PATH@@": profile.dependencies.provenance_path,
        "@@HEALTH_STATUS_POINTER_LITERAL@@": shell_literal(
            profile.health.status_pointer
        ),
        "@@HEALTH_STATUS_VALUE@@": profile.health.status_value,
        "@@HEALTH_TOKEN_POINTER_LITERAL@@": shell_literal(
            profile.health.deployment_token_pointer
        ),
        "@@HEALTH_ROUTE_LITERAL@@": shell_literal(profile.health.route),
        "@@RELEASE_RETENTION_COUNT@@": str(profile.retention.releases),
        "@@AUDIT_RETENTION_DAYS@@": str(profile.retention.audit_days),
        "@@HEALTH_RETRIES@@": str(health_retries),
        "@@HEALTH_CONFIRMATIONS@@": str(profile.health.confirmations),
        "@@MANIFEST_ARRAY@@": shell_array(profile.dependencies.manifests),
        "@@STATIC_PATH_ARRAY@@": shell_array(profile.static_content.release_paths),
        "@@ALLOWED_ROOT_ARRAY@@": shell_array(allowed_candidate_roots(profile)),
        "@@CANDIDATE_SERVICE@@": profile.services.candidate,
        "@@CANDIDATE_UNIT_PREFIX@@": candidate_prefix,
        "@@CANDIDATE_RUNTIME_PREFIX@@": f"{profile.paths.runtime_root}-candidate",
        "@@APPLICATION_TAG@@": identity.tag,
        "@@CONFIG_ROOT@@": profile.paths.config_root,
        "@@RUNTIME_ROOT@@": profile.paths.runtime_root,
        "@@RUNTIME_DIRECTORY@@": profile.paths.runtime_root.removeprefix("/run/"),
        "@@PID_FILE_NAME@@": profile.runtime.pid_file_name,
        "@@SOCKET_NAME@@": profile.runtime.socket_name,
        "@@RECOVERY_SERVICE@@": profile.services.recovery,
        "@@FINALIZER_SERVICE@@": profile.services.finalizer,
        "@@FINALIZER_PATH@@": profile.services.path,
        "@@FAILURE_SERVICE_PREFIX@@": failure_service(profile).removesuffix(
            ".service"
        ),
        "@@FAILURE_SERVICE@@": failure_service(profile),
        "@@GATEWAY_PROGRAM@@": gateway_program(profile),
        "@@NETWORK_POLICY@@": network_policy(profile),
        "@@MEMORY_HIGH_BYTES@@": str(profile.limits.memory_high_bytes),
        "@@MEMORY_MAX_BYTES@@": str(profile.limits.memory_max_bytes),
        "@@TASKS_MAX@@": str(profile.limits.tasks_max),
        "@@CANDIDATE_RUNTIME_MAX_SECONDS@@": str(
            profile.health.timeout_seconds + 60
        ),
        "@@FINALIZER_TIMEOUT_STOP_SECONDS@@": str(
            profile.health.timeout_seconds * 2 + 120
        ),
        "@@SELINUX_MODULE@@": f"{identity.tag.replace('-', '_')}_runtime",
        "@@SELINUX_STATIC_PATTERN_ARRAY@@": shell_array(
            selinux_static_patterns(profile)
        ),
        "@@SELINUX_POINTER_PATTERN_ARRAY@@": shell_array(
            selinux_pointer_patterns(profile)
        ),
        "@@SELINUX_RUNTIME_PATTERN_ARRAY@@": shell_array(
            (
                f"{re.escape(profile.paths.runtime_root)}(/.*)?",
                f"{re.escape('/var' + profile.paths.runtime_root)}(/.*)?",
            )
        ),
        "@@SELINUX_TRIGGER_PATTERN_LITERAL@@": shell_literal(
            f"{re.escape(profile.paths.trigger_root)}/inbox(/.*)?"
        ),
        "@@SELINUX_RELABEL_PATH_ARRAY@@": shell_array(
            selinux_relabel_paths(profile)
        ),
        "@@SOURCE_REVISION@@": source_revision,
        "@@BUNDLE_ID@@": f"{source_revision}-{profile.digest()}",
        "@@PROFILE_DIGEST@@": profile.digest(),
        "@@SSH_POLICY_NAME@@": f"40-{identity.tag}-deploy.conf",
        "@@SSH_MAINTENANCE_NAME@@": f"00-{identity.tag}-deploy-maintenance.conf",
        "@@SYSTEMD_UNIT_ARRAY@@": shell_array(systemd_units(profile)),
        "@@SECURE_SCRIPT_ARRAY@@": shell_array(secure_scripts()),
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
    replacements = build_replacements(profile, source_revision)
    files = [
        RenderedFile(
            spec.relative_path,
            render_template(spec.template_name, replacements),
            spec.mode,
        )
        for spec in template_specs(profile)
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


def normalize_directory_modes(root: Path) -> None:
    """Set deterministic non-writable modes on every rendered directory."""

    for directory, names, _ in os.walk(root, topdown=True, followlinks=False):
        path = Path(directory)
        path.chmod(0o755)
        names.sort()


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
        normalize_directory_modes(staging)
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
