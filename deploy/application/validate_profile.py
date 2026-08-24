#!/usr/bin/env python3
"""Validate and canonically fingerprint gold deployment project profiles."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final, Mapping, NoReturn, Sequence


MAX_PROFILE_BYTES: Final = 64 * 1024
SCHEMA_VERSION: Final = 1
STANDARD_VERSION: Final = "1"
MIN_MEMORY_BYTES: Final = 64 * 1024 * 1024
MIN_TASKS: Final = 16
MAX_TASKS: Final = 65_535
MIN_HEALTH_TIMEOUT_SECONDS: Final = 5
MAX_HEALTH_TIMEOUT_SECONDS: Final = 600
MIN_AUDIT_DAYS: Final = 30
MAX_RETENTION_DAYS: Final = 3_650
IDENTIFIER_PATTERN: Final = re.compile(r"^[a-z_][a-z0-9_-]{0,30}$")
COMMAND_PATTERN: Final = re.compile(r"^[a-z][a-z0-9-]{2,63}$")
UNIT_PATTERN: Final = re.compile(r"^[a-z_][a-z0-9_-]{0,62}\.service$")
TEMPLATE_UNIT_PATTERN: Final = re.compile(r"^[a-z_][a-z0-9_-]{0,62}@\.service$")
PATH_UNIT_PATTERN: Final = re.compile(r"^[a-z_][a-z0-9_-]{0,62}\.path$")
ROUTE_PATTERN: Final = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/-]{0,255}$")
JSON_POINTER_PATTERN: Final = re.compile(r"^(?:/(?:[^~/]|~0|~1)+)+$")
RELATIVE_COMPONENT_PATTERN: Final = re.compile(r"^[A-Za-z0-9._@+-]+$")
FILE_NAME_PATTERN: Final = re.compile(r"^[A-Za-z0-9._+-]{1,128}$")
STATUS_VALUE_PATTERN: Final = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
SUPPORTED_DEPENDENCY_ADAPTERS: Final = frozenset({"node-npm"})
SUPPORTED_RUNTIME_ADAPTERS: Final = frozenset({"node"})
SUPPORTED_BUILD_VALIDATORS: Final = frozenset({"standard-node"})
NODE_MANIFESTS: Final = ("package-lock.json", "package.json")
ROOT_PATH: Final = PurePosixPath("/")

TOP_LEVEL_KEYS: Final = frozenset(
    {
        "schemaVersion",
        "standardVersion",
        "application",
        "paths",
        "runtime",
        "transport",
        "services",
        "health",
        "metadata",
        "dependencies",
        "staticContent",
        "limits",
        "retention",
    }
)


class ProfileError(ValueError):
    """Raised when a deployment profile violates the standard schema."""


def reject_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    """Build a JSON object while refusing ambiguous duplicate member names."""

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProfileError(f"duplicate JSON member: {key}")
        result[key] = value
    return result


def fail(message: str) -> NoReturn:
    """Raise one deterministic profile validation error."""

    raise ProfileError(message)


def require_mapping(value: Any, field: str) -> Mapping[str, Any]:
    """Return a mapping value or fail with its profile field name."""

    if not isinstance(value, dict):
        fail(f"{field} must be an object")
    return value


def require_exact_keys(
    value: Mapping[str, Any], field: str, expected: frozenset[str]
) -> None:
    """Reject missing and unknown object members."""

    actual = frozenset(value)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing:
        fail(f"{field} is missing: {', '.join(missing)}")
    if unknown:
        fail(f"{field} contains unknown fields: {', '.join(unknown)}")


def require_string(value: Any, field: str, pattern: re.Pattern[str]) -> str:
    """Return a bounded string matching the supplied full-match pattern."""

    if not isinstance(value, str) or not pattern.fullmatch(value):
        fail(f"{field} has an invalid value")
    return value


def require_integer(value: Any, field: str, minimum: int, maximum: int) -> int:
    """Return a non-boolean integer within an inclusive range."""

    if isinstance(value, bool) or not isinstance(value, int):
        fail(f"{field} must be an integer")
    if not minimum <= value <= maximum:
        fail(f"{field} must be between {minimum} and {maximum}")
    return value


def require_absolute_path(value: Any, field: str) -> str:
    """Return a normalized absolute path without root or traversal aliases."""

    if not isinstance(value, str) or not value.startswith("/"):
        fail(f"{field} must be an absolute path")
    path = PurePosixPath(value)
    if path == ROOT_PATH or str(path) != value or ".." in path.parts:
        fail(f"{field} must be a normalized non-root absolute path")
    for component in path.parts[1:]:
        if not RELATIVE_COMPONENT_PATTERN.fullmatch(component):
            fail(f"{field} contains an invalid path component")
    return value


def require_relative_path(value: Any, field: str) -> str:
    """Return a normalized relative path without empty or traversal aliases."""

    if not isinstance(value, str) or not value or value.startswith("/"):
        fail(f"{field} must be a relative path")
    path = PurePosixPath(value)
    if str(path) != value or any(part in {".", ".."} for part in path.parts):
        fail(f"{field} must be a normalized relative path")
    for component in path.parts:
        if not RELATIVE_COMPONENT_PATTERN.fullmatch(component):
            fail(f"{field} contains an invalid path component")
    return value


def require_unique_relative_paths(value: Any, field: str, limit: int) -> tuple[str, ...]:
    """Return a non-empty, bounded sequence of unique relative paths."""

    if not isinstance(value, list) or not 1 <= len(value) <= limit:
        fail(f"{field} must contain between 1 and {limit} paths")
    paths = tuple(
        require_relative_path(item, f"{field}[{index}]")
        for index, item in enumerate(value)
    )
    if len(paths) != len(set(paths)):
        fail(f"{field} must not contain duplicate paths")
    if list(paths) != sorted(paths):
        fail(f"{field} must be sorted")
    return paths


@dataclass(frozen=True)
class ApplicationIdentity:
    """Accounts and stable tag assigned to one deployed application."""

    tag: str
    service_user: str
    service_group: str
    deploy_user: str
    deploy_group: str


@dataclass(frozen=True)
class ApplicationPaths:
    """Trusted roots assigned to one application deployment."""

    application_root: str
    secure_root: str
    config_root: str
    runtime_root: str
    trigger_root: str
    dependency_state_root: str
    dependency_cache_root: str


@dataclass(frozen=True)
class RuntimePolicy:
    """Fixed Node process and network behavior."""

    adapter: str
    entrypoint: str
    socket_name: str
    pid_file_name: str
    allow_loopback: bool


@dataclass(frozen=True)
class TransportPolicy:
    """Restricted SSH transaction command."""

    session_command: str


@dataclass(frozen=True)
class ServiceUnits:
    """Systemd units rendered for one application."""

    live: str
    candidate: str
    finalizer: str
    path: str
    recovery: str


@dataclass(frozen=True)
class HealthPolicy:
    """Token-specific Unix-socket readiness contract."""

    route: str
    status_pointer: str
    status_value: str
    deployment_token_pointer: str
    confirmations: int
    timeout_seconds: int


@dataclass(frozen=True)
class MetadataPolicy:
    """Immutable deployment metadata contract."""

    path: str
    deployment_token_pointer: str


@dataclass(frozen=True)
class DependencyPolicy:
    """Bounded dependency input and provenance contract."""

    adapter: str
    manifests: tuple[str, ...]
    provenance_path: str


@dataclass(frozen=True)
class StaticContentPolicy:
    """Explicit static release paths and fixed validator."""

    release_paths: tuple[str, ...]
    build_validator: str


@dataclass(frozen=True)
class ResourceLimits:
    """Numeric systemd resource limits."""

    memory_high_bytes: int
    memory_max_bytes: int
    tasks_max: int


@dataclass(frozen=True)
class RetentionPolicy:
    """Immutable release and append-only audit retention."""

    releases: int
    audit_days: int


@dataclass(frozen=True)
class ApplicationProfile:
    """Validated immutable representation of a gold deployment profile."""

    source: Mapping[str, Any]
    identity: ApplicationIdentity
    paths: ApplicationPaths
    runtime: RuntimePolicy
    transport: TransportPolicy
    services: ServiceUnits
    health: HealthPolicy
    metadata: MetadataPolicy
    dependencies: DependencyPolicy
    static_content: StaticContentPolicy
    limits: ResourceLimits
    retention: RetentionPolicy

    def canonical_bytes(self) -> bytes:
        """Return deterministic UTF-8 JSON used for bundle provenance."""

        return (
            json.dumps(
                self.source,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")

    def digest(self) -> str:
        """Return the SHA-256 digest of the canonical profile."""

        return hashlib.sha256(self.canonical_bytes()).hexdigest()


def validate_identity(profile: Mapping[str, Any]) -> ApplicationIdentity:
    """Validate the application identity section and trust separation."""

    value = require_mapping(profile["application"], "application")
    keys = frozenset(
        {"tag", "serviceUser", "serviceGroup", "deployUser", "deployGroup"}
    )
    require_exact_keys(value, "application", keys)
    identity = ApplicationIdentity(
        tag=require_string(value["tag"], "application.tag", IDENTIFIER_PATTERN),
        service_user=require_string(
            value["serviceUser"], "application.serviceUser", IDENTIFIER_PATTERN
        ),
        service_group=require_string(
            value["serviceGroup"], "application.serviceGroup", IDENTIFIER_PATTERN
        ),
        deploy_user=require_string(
            value["deployUser"], "application.deployUser", IDENTIFIER_PATTERN
        ),
        deploy_group=require_string(
            value["deployGroup"], "application.deployGroup", IDENTIFIER_PATTERN
        ),
    )
    if identity.service_user == identity.deploy_user:
        fail("application serviceUser and deployUser must be distinct")
    if identity.service_group == identity.deploy_group:
        fail("application serviceGroup and deployGroup must be distinct")
    return identity


def validate_paths(profile: Mapping[str, Any]) -> ApplicationPaths:
    """Validate all trusted absolute roots and reject overlapping authority."""

    value = require_mapping(profile["paths"], "paths")
    keys = frozenset(
        {
            "applicationRoot",
            "secureRoot",
            "configRoot",
            "runtimeRoot",
            "triggerRoot",
            "dependencyStateRoot",
            "dependencyCacheRoot",
        }
    )
    require_exact_keys(value, "paths", keys)
    paths = {
        key: require_absolute_path(item, f"paths.{key}")
        for key, item in value.items()
    }
    if len(paths.values()) != len(set(paths.values())):
        fail("paths must identify distinct roots")
    named_paths = [(name, PurePosixPath(path)) for name, path in paths.items()]
    for index, (left_name, left_path) in enumerate(named_paths):
        for right_name, right_path in named_paths[index + 1 :]:
            if left_path in right_path.parents or right_path in left_path.parents:
                fail(f"paths.{left_name} and paths.{right_name} must not contain each other")
    return ApplicationPaths(
        application_root=paths["applicationRoot"],
        secure_root=paths["secureRoot"],
        config_root=paths["configRoot"],
        runtime_root=paths["runtimeRoot"],
        trigger_root=paths["triggerRoot"],
        dependency_state_root=paths["dependencyStateRoot"],
        dependency_cache_root=paths["dependencyCacheRoot"],
    )


def validate_runtime(profile: Mapping[str, Any]) -> RuntimePolicy:
    """Validate the fixed Node runtime and loopback policy."""

    value = require_mapping(profile["runtime"], "runtime")
    keys = frozenset(
        {"adapter", "entrypoint", "socketName", "pidFileName", "allowLoopback"}
    )
    require_exact_keys(value, "runtime", keys)
    adapter = value["adapter"]
    if adapter not in SUPPORTED_RUNTIME_ADAPTERS:
        fail("runtime.adapter is unsupported")
    allow_loopback = value["allowLoopback"]
    if not isinstance(allow_loopback, bool):
        fail("runtime.allowLoopback must be a boolean")
    return RuntimePolicy(
        adapter=adapter,
        entrypoint=require_relative_path(value["entrypoint"], "runtime.entrypoint"),
        socket_name=require_string(
            value["socketName"], "runtime.socketName", FILE_NAME_PATTERN
        ),
        pid_file_name=require_string(
            value["pidFileName"], "runtime.pidFileName", FILE_NAME_PATTERN
        ),
        allow_loopback=allow_loopback,
    )


def validate_transport(profile: Mapping[str, Any]) -> TransportPolicy:
    """Validate the single accepted forced-command session name."""

    value = require_mapping(profile["transport"], "transport")
    require_exact_keys(value, "transport", frozenset({"sessionCommand"}))
    return TransportPolicy(
        session_command=require_string(
            value["sessionCommand"], "transport.sessionCommand", COMMAND_PATTERN
        )
    )


def validate_services(profile: Mapping[str, Any]) -> ServiceUnits:
    """Validate all required systemd units and the isolated candidate unit."""

    value = require_mapping(profile["services"], "services")
    keys = frozenset({"live", "candidate", "finalizer", "path", "recovery"})
    require_exact_keys(value, "services", keys)
    units = (
        require_string(value["live"], "services.live", UNIT_PATTERN),
        require_string(value["candidate"], "services.candidate", TEMPLATE_UNIT_PATTERN),
        require_string(value["finalizer"], "services.finalizer", UNIT_PATTERN),
        require_string(value["path"], "services.path", PATH_UNIT_PATTERN),
        require_string(value["recovery"], "services.recovery", UNIT_PATTERN),
    )
    if len(units) != len(set(units)):
        fail("services must identify distinct units")
    return ServiceUnits(*units)


def validate_health(profile: Mapping[str, Any]) -> HealthPolicy:
    """Validate token-specific Unix-socket health policy."""

    value = require_mapping(profile["health"], "health")
    keys = frozenset(
        {
            "route",
            "statusPointer",
            "statusValue",
            "deploymentTokenPointer",
            "confirmations",
            "timeoutSeconds",
        }
    )
    require_exact_keys(value, "health", keys)
    health = HealthPolicy(
        route=require_string(value["route"], "health.route", ROUTE_PATTERN),
        status_pointer=require_string(
            value["statusPointer"], "health.statusPointer", JSON_POINTER_PATTERN
        ),
        status_value=require_string(
            value["statusValue"], "health.statusValue", STATUS_VALUE_PATTERN
        ),
        deployment_token_pointer=require_string(
            value["deploymentTokenPointer"],
            "health.deploymentTokenPointer",
            JSON_POINTER_PATTERN,
        ),
        confirmations=require_integer(
            value["confirmations"], "health.confirmations", 1, 10
        ),
        timeout_seconds=require_integer(
            value["timeoutSeconds"],
            "health.timeoutSeconds",
            MIN_HEALTH_TIMEOUT_SECONDS,
            MAX_HEALTH_TIMEOUT_SECONDS,
        ),
    )
    if health.confirmations * 2 > health.timeout_seconds:
        fail("health.timeoutSeconds must allow two seconds per confirmation")
    return health


def validate_metadata(profile: Mapping[str, Any]) -> MetadataPolicy:
    """Validate immutable deployment metadata location and token field."""

    value = require_mapping(profile["metadata"], "metadata")
    keys = frozenset({"path", "deploymentTokenPointer"})
    require_exact_keys(value, "metadata", keys)
    return MetadataPolicy(
        path=require_relative_path(value["path"], "metadata.path"),
        deployment_token_pointer=require_string(
            value["deploymentTokenPointer"],
            "metadata.deploymentTokenPointer",
            JSON_POINTER_PATTERN,
        ),
    )


def validate_dependencies(profile: Mapping[str, Any]) -> DependencyPolicy:
    """Validate the bounded dependency adapter and manifest inputs."""

    value = require_mapping(profile["dependencies"], "dependencies")
    keys = frozenset({"adapter", "manifests", "provenancePath"})
    require_exact_keys(value, "dependencies", keys)
    adapter = value["adapter"]
    if adapter not in SUPPORTED_DEPENDENCY_ADAPTERS:
        fail("dependencies.adapter is unsupported")
    manifests = require_unique_relative_paths(value["manifests"], "dependencies.manifests", 8)
    if manifests != NODE_MANIFESTS:
        fail("node-npm dependencies.manifests must be package-lock.json and package.json")
    return DependencyPolicy(
        adapter=adapter,
        manifests=manifests,
        provenance_path=require_relative_path(
            value["provenancePath"], "dependencies.provenancePath"
        ),
    )


def validate_static_content(profile: Mapping[str, Any]) -> StaticContentPolicy:
    """Validate explicit static paths and the named build adapter."""

    value = require_mapping(profile["staticContent"], "staticContent")
    keys = frozenset({"releasePaths", "buildValidator"})
    require_exact_keys(value, "staticContent", keys)
    release_paths = require_unique_relative_paths(
        value["releasePaths"], "staticContent.releasePaths", 32
    )
    build_validator = value["buildValidator"]
    if build_validator not in SUPPORTED_BUILD_VALIDATORS:
        fail("staticContent.buildValidator is unsupported")
    return StaticContentPolicy(
        release_paths=release_paths, build_validator=build_validator
    )


def validate_limits(profile: Mapping[str, Any]) -> ResourceLimits:
    """Validate numeric systemd resource boundaries."""

    value = require_mapping(profile["limits"], "limits")
    keys = frozenset({"memoryHighBytes", "memoryMaxBytes", "tasksMax"})
    require_exact_keys(value, "limits", keys)
    memory_high = require_integer(
        value["memoryHighBytes"], "limits.memoryHighBytes", MIN_MEMORY_BYTES, sys.maxsize
    )
    memory_max = require_integer(
        value["memoryMaxBytes"], "limits.memoryMaxBytes", MIN_MEMORY_BYTES, sys.maxsize
    )
    if memory_high > memory_max:
        fail("limits.memoryHighBytes must not exceed limits.memoryMaxBytes")
    return ResourceLimits(
        memory_high_bytes=memory_high,
        memory_max_bytes=memory_max,
        tasks_max=require_integer(
            value["tasksMax"], "limits.tasksMax", MIN_TASKS, MAX_TASKS
        ),
    )


def validate_retention(profile: Mapping[str, Any]) -> RetentionPolicy:
    """Validate bounded release and audit retention policy."""

    value = require_mapping(profile["retention"], "retention")
    keys = frozenset({"releases", "auditDays"})
    require_exact_keys(value, "retention", keys)
    return RetentionPolicy(
        releases=require_integer(value["releases"], "retention.releases", 2, 100),
        audit_days=require_integer(
            value["auditDays"],
            "retention.auditDays",
            MIN_AUDIT_DAYS,
            MAX_RETENTION_DAYS,
        ),
    )


def validate_profile(source: Mapping[str, Any]) -> ApplicationProfile:
    """Validate a decoded deployment profile and return its typed identity."""

    require_exact_keys(source, "profile", TOP_LEVEL_KEYS)
    if source["schemaVersion"] != SCHEMA_VERSION:
        fail(f"schemaVersion must be {SCHEMA_VERSION}")
    if source["standardVersion"] != STANDARD_VERSION:
        fail(f"standardVersion must be {STANDARD_VERSION}")
    return ApplicationProfile(
        source=source,
        identity=validate_identity(source),
        paths=validate_paths(source),
        runtime=validate_runtime(source),
        transport=validate_transport(source),
        services=validate_services(source),
        health=validate_health(source),
        metadata=validate_metadata(source),
        dependencies=validate_dependencies(source),
        static_content=validate_static_content(source),
        limits=validate_limits(source),
        retention=validate_retention(source),
    )


def read_bounded_regular_file(path: Path, maximum_bytes: int, purpose: str) -> bytes:
    """Read one bounded, inode-stable, single-link file without following links."""

    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        fail(f"cannot open {purpose} as a real regular file: {path}: {error.strerror}")
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            fail(f"{purpose} must be a one-link real regular file: {path}")
        if before.st_size <= 0 or before.st_size > maximum_bytes:
            fail(f"{purpose} size must be between 1 and {maximum_bytes} bytes: {path}")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 16 * 1024))
            if not chunk:
                fail(f"{purpose} changed while being read: {path}")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            fail(f"{purpose} grew while being read: {path}")
        after = os.fstat(descriptor)
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
        if identity_before != identity_after:
            fail(f"{purpose} changed while being read: {path}")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def load_profile(path: Path) -> ApplicationProfile:
    """Read one bounded real JSON file and validate its complete profile."""

    try:
        raw = read_bounded_regular_file(path, MAX_PROFILE_BYTES, "profile")
        decoded = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"cannot decode profile {path}: {error}")
    return validate_profile(require_mapping(decoded, "profile"))


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""

    parser = argparse.ArgumentParser(
        description="Validate and fingerprint gold deployment project profiles."
    )
    parser.add_argument(
        "--print-canonical",
        action="store_true",
        help="write canonical JSON instead of one digest line per profile",
    )
    parser.add_argument("profiles", metavar="PROFILE", nargs="+", type=Path)
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    """Run the profile validator command."""

    options = build_parser().parse_args(arguments)
    try:
        for path in options.profiles:
            profile = load_profile(path)
            if options.print_canonical:
                sys.stdout.buffer.write(profile.canonical_bytes())
            else:
                print(f"{path}: sha256:{profile.digest()}")
    except ProfileError as error:
        print(f"validate-profile: ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
