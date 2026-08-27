#!/usr/bin/env python3

#
# Author: Michael Welter <me@mikinho.com> - https://github.com/mikinho
#

"""Focused hostile-input tests for gold deployment project profiles."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from typing import Any, Final


REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[1]
APPLICATION_ROOT: Final = REPOSITORY_ROOT / "deploy" / "application"
VALIDATOR_PATH: Final = APPLICATION_ROOT / "validate_profile.py"
PROFILE_ROOT: Final = APPLICATION_ROOT / "profiles"
RENDERER_PATH: Final = APPLICATION_ROOT / "render_core.py"
CONFORMANCE_PATH: Final = APPLICATION_ROOT / "verify_bundle.py"
EXAMPLE_PROFILE: Final = PROFILE_ROOT / "example_node_app.json"
SOURCE_REVISION: Final = "0123456789abcdef0123456789abcdef01234567"


def load_validator() -> ModuleType:
    """Load the repository validator without changing Python import paths."""

    spec = importlib.util.spec_from_file_location("deployment_profile", VALIDATOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load validator: {VALIDATOR_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


VALIDATOR: Final = load_validator()


class ProfileValidationTests(unittest.TestCase):
    """Verify valid references and fail-closed profile parsing."""

    def setUp(self) -> None:
        """Load one independent valid profile for mutation tests."""

        self.source: dict[str, Any] = json.loads(EXAMPLE_PROFILE.read_text(encoding="utf-8"))

    def assert_invalid(self, source: dict[str, Any], message: str) -> None:
        """Assert that one decoded profile fails with a stable message fragment."""

        with self.assertRaisesRegex(VALIDATOR.ProfileError, message):
            VALIDATOR.validate_profile(source)

    def test_public_example_profile_validates(self) -> None:
        """The synthetic public profile conforms to the complete schema."""

        profile = VALIDATOR.load_profile(EXAMPLE_PROFILE)
        self.assertEqual(profile.identity.tag, "example_node_app")
        self.assertEqual(profile.runtime.entrypoint, "src/web.js")
        self.assertEqual(profile.health.status_value, "healthy")
        self.assertRegex(profile.digest(), r"^[0-9a-f]{64}$")

    def test_unknown_fields_are_rejected(self) -> None:
        """Profiles cannot smuggle unreviewed behavior through extension fields."""

        source = copy.deepcopy(self.source)
        source["transport"]["shell"] = "/bin/bash"
        self.assert_invalid(source, "transport contains unknown fields: shell")

    def test_service_and_deploy_accounts_must_remain_distinct(self) -> None:
        """A profile cannot collapse transfer and runtime authority."""

        source = copy.deepcopy(self.source)
        source["application"]["deployUser"] = source["application"]["serviceUser"]
        self.assert_invalid(source, "serviceUser and deployUser must be distinct")

        source = copy.deepcopy(self.source)
        source["application"]["deployGroup"] = source["application"]["serviceGroup"]
        self.assert_invalid(source, "serviceGroup and deployGroup must be distinct")

    def test_traversal_and_path_aliases_are_rejected(self) -> None:
        """Trusted roots and release-relative paths must be canonical."""

        source = copy.deepcopy(self.source)
        source["paths"]["applicationRoot"] = "/opt/../etc"
        self.assert_invalid(source, "applicationRoot must be a normalized")

        source = copy.deepcopy(self.source)
        source["metadata"]["path"] = ".build/../package.json"
        self.assert_invalid(source, "metadata.path must be a normalized")

    def test_overlapping_application_and_secure_roots_are_rejected(self) -> None:
        """Deploy-writable application state cannot contain trusted scripts."""

        source = copy.deepcopy(self.source)
        source["paths"]["secureRoot"] = "/opt/example_node_app/.secure"
        self.assert_invalid(source, "applicationRoot and paths.secureRoot")

    def test_runtime_root_must_be_managed_directly_beneath_run(self) -> None:
        """RuntimeDirectory rendering cannot escape or alias systemd's /run root."""

        source = copy.deepcopy(self.source)
        source["paths"]["runtimeRoot"] = "/var/lib/example_runtime"
        self.assert_invalid(source, "must be one direct child of /run")

        source = copy.deepcopy(self.source)
        source["paths"]["runtimeRoot"] = "/run/nested/example_runtime"
        self.assert_invalid(source, "must be one direct child of /run")

    def test_unsorted_path_sets_are_rejected(self) -> None:
        """Semantically equal profiles have one canonical path ordering."""

        source = copy.deepcopy(self.source)
        source["dependencies"]["manifests"] = ["package.json", "package-lock.json"]
        self.assert_invalid(source, "dependencies.manifests must be sorted")

    def test_resource_high_water_mark_cannot_exceed_hard_limit(self) -> None:
        """Profiles reject internally inconsistent systemd memory policy."""

        source = copy.deepcopy(self.source)
        source["limits"]["memoryHighBytes"] = source["limits"]["memoryMaxBytes"] + 1
        self.assert_invalid(source, "memoryHighBytes must not exceed")

    def test_health_timeout_must_fit_required_confirmations(self) -> None:
        """A profile cannot demand more confirmations than its health window permits."""

        source = copy.deepcopy(self.source)
        source["health"]["confirmations"] = 10
        source["health"]["timeoutSeconds"] = 5
        self.assert_invalid(source, "must allow two seconds per confirmation")

    def test_runtime_and_build_adapters_are_closed_sets(self) -> None:
        """Profiles cannot select unreviewed executable behavior."""

        source = copy.deepcopy(self.source)
        source["runtime"]["adapter"] = "arbitrary-command"
        self.assert_invalid(source, "runtime.adapter is unsupported")

        source = copy.deepcopy(self.source)
        source["staticContent"]["buildValidator"] = "private-script"
        self.assert_invalid(source, "buildValidator is unsupported")

    def test_node_dependency_manifest_contract_is_exact(self) -> None:
        """The fixed snapshot and hash helpers receive exactly their reviewed inputs."""

        source = copy.deepcopy(self.source)
        source["dependencies"]["manifests"] = [
            "npm-shrinkwrap.json",
            "package.json",
        ]
        self.assert_invalid(source, "must be package-lock.json and package.json")

    def test_duplicate_json_members_are_rejected(self) -> None:
        """A decoded profile has one unambiguous interpretation."""

        duplicate = '{"schemaVersion":1,"schemaVersion":1}'
        with tempfile.TemporaryDirectory() as directory:
            profile_path = Path(directory) / "duplicate.json"
            profile_path.write_text(duplicate, encoding="utf-8")
            with self.assertRaisesRegex(VALIDATOR.ProfileError, "duplicate JSON member"):
                VALIDATOR.load_profile(profile_path)

    def test_profile_symlinks_are_rejected(self) -> None:
        """Profile validation never follows an alias to mutable input."""

        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            profile_path = directory_path / "profile.json"
            profile_path.write_text(json.dumps(self.source), encoding="utf-8")
            link_path = directory_path / "profile-link.json"
            link_path.symlink_to(profile_path)
            with self.assertRaisesRegex(VALIDATOR.ProfileError, "real regular file"):
                VALIDATOR.load_profile(link_path)

    def test_profile_hardlinks_are_rejected(self) -> None:
        """Profile identity cannot be shared with another mutable directory entry."""

        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            profile_path = directory_path / "profile.json"
            profile_path.write_text(json.dumps(self.source), encoding="utf-8")
            link_path = directory_path / "profile-hardlink.json"
            link_path.hardlink_to(profile_path)
            with self.assertRaisesRegex(VALIDATOR.ProfileError, "one-link real regular file"):
                VALIDATOR.load_profile(profile_path)


class CoreRendererTests(unittest.TestCase):
    """Verify deterministic, provenance-bound transaction-core rendering."""

    def render(
        self,
        output: Path,
        revision: str = SOURCE_REVISION,
        profile: Path = EXAMPLE_PROFILE,
    ) -> subprocess.CompletedProcess[str]:
        """Run the public renderer against the synthetic example profile."""

        return subprocess.run(
            [
                sys.executable,
                str(RENDERER_PATH),
                "--profile",
                str(profile),
                "--source-revision",
                revision,
                "--output",
                str(output),
            ],
            check=False,
            capture_output=True,
            text=True,
        )

    def verify(
        self, bundle: Path, revision: str = SOURCE_REVISION
    ) -> subprocess.CompletedProcess[str]:
        """Run vendored-bundle conformance against the synthetic profile."""

        return subprocess.run(
            [
                sys.executable,
                str(CONFORMANCE_PATH),
                "--profile",
                str(EXAMPLE_PROFILE),
                "--source-revision",
                revision,
                "--bundle",
                str(bundle),
            ],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_core_bundle_is_reproducible_and_self_verifying(self) -> None:
        """Two renders produce identical bytes, modes, and checksum evidence."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first"
            second = root / "second"
            for output in (first, second):
                result = self.render(output)
                self.assertEqual(result.returncode, 0, result.stderr)

            first_paths = sorted(
                path.relative_to(first) for path in first.rglob("*") if path.is_file()
            )
            second_paths = sorted(
                path.relative_to(second) for path in second.rglob("*") if path.is_file()
            )
            self.assertEqual(first_paths, second_paths)
            for relative_path in first_paths:
                first_path = first / relative_path
                second_path = second / relative_path
                self.assertEqual(first_path.read_bytes(), second_path.read_bytes())
                self.assertEqual(
                    stat.S_IMODE(first_path.stat().st_mode),
                    stat.S_IMODE(second_path.stat().st_mode),
                )

            manifest = json.loads((first / "STANDARD-MANIFEST.json").read_text())
            self.assertEqual(manifest["conformanceStatus"], "core-only")
            self.assertEqual(manifest["sourceRevision"], SOURCE_REVISION)
            self.assertEqual(manifest["standardVersion"], "1")
            self.assertEqual(stat.S_IMODE(first.stat().st_mode), 0o755)

            checksum_lines = (first / "SHA256SUMS").read_text().splitlines()
            self.assertGreaterEqual(len(checksum_lines), 5)
            for line in checksum_lines:
                expected_digest, relative_path = line.split("  ", maxsplit=1)
                actual_digest = hashlib.sha256((first / relative_path).read_bytes()).hexdigest()
                self.assertEqual(actual_digest, expected_digest)

            for script_path in sorted((first / "scripts").glob("*.py")):
                self.assertEqual(stat.S_IMODE(script_path.stat().st_mode), 0o755)
                source = script_path.read_text(encoding="utf-8")
                self.assertNotIn("@@", source)
                compile(source, str(script_path), "exec")

            finalizer = first / "scripts" / "post-deploy"
            self.assertEqual(stat.S_IMODE(finalizer.stat().st_mode), 0o755)
            finalizer_source = finalizer.read_text(encoding="utf-8")
            self.assertNotIn("@@", finalizer_source)
            self.assertIn(
                r"^(manual|[0-9a-f]{40,64})-[0-9]{1,20}-[0-9a-f]{24}$",
                finalizer_source,
            )
            self.assertIn("example_node_app-candidate@.service", finalizer_source)
            self.assertNotIn("legacy release", finalizer_source)
            shell_check = subprocess.run(
                ["bash", "-n", str(finalizer)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(shell_check.returncode, 0, shell_check.stderr)

            for relative_path in (
                "setup",
                "scripts/selinux-setup",
                "scripts/verify-host",
            ):
                script = first / relative_path
                self.assertEqual(stat.S_IMODE(script.stat().st_mode), 0o755)
                source = script.read_text(encoding="utf-8")
                self.assertNotIn("@@", source)
                shell_check = subprocess.run(
                    ["bash", "-n", str(script)],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(shell_check.returncode, 0, shell_check.stderr)

            node_check = subprocess.run(
                ["node", "--check", str(first / "scripts" / "package-hash.mjs")],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(node_check.returncode, 0, node_check.stderr)

    def test_dependency_hash_tracks_only_production_install_state(self) -> None:
        """The rendered npm adapter ignores dev-only drift and detects production drift."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "bundle"
            result = self.render(bundle)
            self.assertEqual(result.returncode, 0, result.stderr)
            application = root / "application"
            application.mkdir()
            package = {
                "name": "example",
                "dependencies": {"runtime-package": "1.0.0"},
                "devDependencies": {"test-package": "1.0.0"},
            }
            lock = {
                "lockfileVersion": 3,
                "requires": True,
                "packages": {
                    "": {
                        "dependencies": {"runtime-package": "1.0.0"},
                        "devDependencies": {"test-package": "1.0.0"},
                    },
                    "node_modules/runtime-package": {"version": "1.0.0"},
                    "node_modules/test-package": {"version": "1.0.0", "dev": True},
                },
            }
            package_path = application / "package.json"
            lock_path = application / "package-lock.json"
            package_path.write_text(json.dumps(package), encoding="utf-8")
            lock_path.write_text(json.dumps(lock), encoding="utf-8")
            script = bundle / "scripts" / "package-hash.mjs"

            def calculate_hash() -> str:
                completed = subprocess.run(
                    ["node", str(script), str(application)],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                digest = completed.stdout.strip()
                self.assertRegex(digest, r"^[0-9a-f]{64}$")
                return digest

            baseline = calculate_hash()
            package["devDependencies"]["test-package"] = "2.0.0"
            package_path.write_text(json.dumps(package), encoding="utf-8")
            self.assertEqual(calculate_hash(), baseline)
            package["dependencies"]["runtime-package"] = "2.0.0"
            package_path.write_text(json.dumps(package), encoding="utf-8")
            self.assertNotEqual(calculate_hash(), baseline)

    def test_renderer_refuses_an_existing_output(self) -> None:
        """A render never overlays an earlier reviewed bundle."""

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "existing"
            output.mkdir()
            result = self.render(output)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("output must not already exist", result.stderr)

    def test_renderer_rejects_an_unbound_source_revision(self) -> None:
        """Bundle provenance always names one immutable standard revision."""

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "invalid"
            result = self.render(output, "main")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("source revision must contain", result.stderr)
            self.assertFalse(output.exists())

    def test_vendored_bundle_conformance_rejects_every_tree_drift(self) -> None:
        """Content, mode, extra-file, alias, and revision drift all fail closed."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pristine = root / "pristine"
            result = self.render(pristine)
            self.assertEqual(result.returncode, 0, result.stderr)
            verified = self.verify(pristine)
            self.assertEqual(verified.returncode, 0, verified.stderr)

            mutations = ("content", "mode", "extra", "symlink")
            for mutation in mutations:
                with self.subTest(mutation=mutation):
                    candidate = root / mutation
                    shutil.copytree(pristine, candidate)
                    if mutation == "content":
                        profile = candidate / "profile.json"
                        profile.write_bytes(profile.read_bytes() + b" ")
                    elif mutation == "mode":
                        (candidate / "scripts/post-deploy").chmod(0o700)
                    elif mutation == "extra":
                        (candidate / "unreviewed").write_text("extra\n")
                    else:
                        profile = candidate / "profile.json"
                        target = root / "attacker-profile.json"
                        target.write_bytes(profile.read_bytes())
                        profile.unlink()
                        profile.symlink_to(target)
                    drift = self.verify(candidate)
                    self.assertNotEqual(drift.returncode, 0)

            wrong_revision = self.verify(pristine, "f" * 40)
            self.assertNotEqual(wrong_revision.returncode, 0)

    def test_finalizer_accepts_only_exact_transaction_tokens(self) -> None:
        """Release pointers cannot widen the gateway's deployment-token grammar."""

        valid_token = f"{SOURCE_REVISION}-1787530000-0123456789abcdef01234567"
        invalid_tokens = (
            "releases/current",
            "manual-1-0123456789abcdef0123456Z",
            f"{SOURCE_REVISION.upper()}-1787530000-0123456789abcdef01234567",
            f"{SOURCE_REVISION}-1787530000-0123456789abcdef01234567/extra",
        )
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "bundle"
            result = self.render(bundle)
            self.assertEqual(result.returncode, 0, result.stderr)
            command = """
                source "$1"
                valid_deploy_id "$2" || exit 11
                shift 2
                for token in "$@"; do
                    ! valid_deploy_id "$token" || exit 12
                done
            """
            completed = subprocess.run(
                [
                    "bash",
                    "-c",
                    command,
                    "finalizer-token-test",
                    str(bundle / "scripts" / "post-deploy"),
                    valid_token,
                    *invalid_tokens,
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_shell_hostile_health_route_remains_literal_data(self) -> None:
        """A schema-valid route with shell metacharacters cannot alter the script."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "route-injection"
            hostile_route = f"/'$HOME;touch$IFS{marker};"
            source = json.loads(EXAMPLE_PROFILE.read_text(encoding="utf-8"))
            source["health"]["route"] = hostile_route
            profile = root / "hostile-profile.json"
            profile.write_text(json.dumps(source), encoding="utf-8")
            bundle = root / "bundle"
            result = self.render(bundle, profile=profile)
            self.assertEqual(result.returncode, 0, result.stderr)
            finalizer = bundle / "scripts" / "post-deploy"
            completed = subprocess.run(
                [
                    "bash",
                    "-c",
                    'source "$1"; printf "%s" "$HEALTH_ROUTE"',
                    "finalizer-route-test",
                    str(finalizer),
                ],
                check=False,
                capture_output=True,
                text=True,
                env={"HOME": "/unexpected", "PATH": os.environ["PATH"]},
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, hostile_route)
            self.assertFalse(marker.exists())

    def test_host_policy_is_hardened_and_profile_bound(self) -> None:
        """Rendered units, SSH policy, and SELinux policy preserve trust separation."""

        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "bundle"
            result = self.render(bundle)
            self.assertEqual(result.returncode, 0, result.stderr)

            expected_paths = {
                "selinux/runtime.te",
                "ssh/deploy.conf",
                "ssh/maintenance.conf",
                "systemd/example_deploy-failure@.service",
                "systemd/example_deploy-recover.service",
                "systemd/example_deploy.path",
                "systemd/example_deploy.service",
                "systemd/example_node_app-candidate@.service",
                "systemd/example_node_app.service",
            }
            manifest = json.loads((bundle / "STANDARD-MANIFEST.json").read_text())
            manifest_paths = {entry["path"] for entry in manifest["files"]}
            self.assertTrue(expected_paths <= manifest_paths)

            live = (bundle / "systemd/example_node_app.service").read_text()
            self.assertIn("Requires=example_deploy-recover.service", live)
            self.assertIn("WorkingDirectory=/opt/example_node_app/current", live)
            self.assertIn("IPAddressDeny=127.0.0.0/8 ::1/128", live)
            self.assertIn("SocketBindDeny=any", live)
            self.assertIn("MemoryHigh=805306368", live)
            self.assertIn("MemoryMax=1073741824", live)
            self.assertIn("TasksMax=128", live)

            candidate = (
                bundle / "systemd/example_node_app-candidate@.service"
            ).read_text()
            self.assertIn(
                "WorkingDirectory=/opt/example_node_app/candidates/%i", candidate
            )
            self.assertIn("RuntimeDirectory=example_node_app-candidate-%i", candidate)
            self.assertIn("Restart=no", candidate)

            finalizer = (bundle / "systemd/example_deploy.service").read_text()
            self.assertIn("ExecStopPost=", finalizer)
            self.assertIn(" --recover", finalizer)
            self.assertIn("OnFailure=example_deploy-failure@%n.service", finalizer)
            self.assertNotIn("RestrictSUIDSGID", finalizer)

            recovery = (
                bundle / "systemd/example_deploy-recover.service"
            ).read_text()
            self.assertIn("Before=example_node_app.service example_deploy.path", recovery)
            self.assertIn("--recover-pointer", recovery)
            self.assertNotIn("ConditionPathExists", recovery)

            ssh_policy = (bundle / "ssh/deploy.conf").read_text()
            self.assertIn("Match User example_deploy", ssh_policy)
            self.assertIn("AuthenticationMethods publickey", ssh_policy)
            self.assertIn("ForceCommand /usr/local/bin/example-node-app-deploy-gateway", ssh_policy)
            self.assertNotIn("PermitUserEnvironment", ssh_policy)

            selinux = (bundle / "selinux/runtime.te").read_text()
            self.assertIn("type example_deploy_trigger_t;", selinux)
            self.assertIn(
                "allow init_t example_deploy_trigger_t:dir { getattr search watch };",
                selinux,
            )
            self.assertNotRegex(
                selinux,
                r"allow init_t example_deploy_trigger_t:(?:dir|file).*\b(?:add_name|create|remove_name|rename|unlink|write)\b",
            )

    def test_loopback_policy_is_an_explicit_profile_decision(self) -> None:
        """Development loopback access changes only the reviewed network block."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = json.loads(EXAMPLE_PROFILE.read_text(encoding="utf-8"))
            source["runtime"]["allowLoopback"] = True
            profile = root / "loopback-profile.json"
            profile.write_text(json.dumps(source), encoding="utf-8")
            bundle = root / "bundle"
            result = self.render(bundle, profile=profile)
            self.assertEqual(result.returncode, 0, result.stderr)
            for relative_path in (
                "systemd/example_node_app.service",
                "systemd/example_node_app-candidate@.service",
            ):
                unit = (bundle / relative_path).read_text()
                self.assertIn("IPAddressAllow=0.0.0.0/0 ::/0", unit)
                self.assertIn("IPAddressDeny=any", unit)
                self.assertNotIn("IPAddressDeny=127.0.0.0/8", unit)

    def test_installer_is_fail_closed_and_verifier_is_non_mutating(self) -> None:
        """Setup arms ingress denial first and only the verifier performs reads."""

        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "bundle"
            result = self.render(bundle)
            self.assertEqual(result.returncode, 0, result.stderr)
            setup = (bundle / "setup").read_text()
            main = setup[setup.index("main() {") :]
            ordered_calls = (
                "require_platform",
                "verify_source_bundle",
                "quiesce_services",
                "ensure_accounts",
                "ensure_directories",
                "install_deploy_key",
                "verify_rsync_source",
                "archive_bundle",
                "install_policy",
                "flock --unlock 8",
                "arm_services",
                "finish_setup",
            )
            positions = [main.index(call) for call in ordered_calls]
            self.assertEqual(positions, sorted(positions))
            require_platform = setup[
                setup.index("require_platform() {") : setup.index("verify_source_bundle() {")
            ]
            self.assertIn("restorecon rpm sesearch sha256sum", require_platform)
            self.assertIn("trap failure_guard ERR EXIT", setup)
            self.assertIn("setup failed; deployment SSH remains denied", setup)
            self.assertIn('"$SECURE_DIR/scripts/verify-host" --pre-ssh', setup)
            self.assertIn('rm -f -- "$SSH_MAINTENANCE"', setup)

            verifier = (bundle / "scripts/verify-host").read_text()
            self.assertNotRegex(
                verifier,
                r"\bsystemctl\s+(?:start|stop|restart|reload|enable|disable|daemon-reload)\b",
            )
            self.assertNotRegex(
                verifier,
                r"(?m)^\s*(?:chown|chmod|install|mv|rm)\s",
            )
            self.assertIn("live Unix-socket health matches current token", verifier)
            self.assertIn("systemd has forbidden trigger-file permission", verifier)
            self.assertIn("effective SSH ForceCommand is wrong", verifier)

    def test_distinct_service_group_is_rendered_consistently(self) -> None:
        """Runtime ownership never assumes that the user and group names match."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = json.loads(EXAMPLE_PROFILE.read_text(encoding="utf-8"))
            source["application"]["serviceGroup"] = "example_runtime"
            profile = root / "distinct-group-profile.json"
            profile.write_text(json.dumps(source), encoding="utf-8")
            bundle = root / "bundle"
            result = self.render(bundle, profile=profile)
            self.assertEqual(result.returncode, 0, result.stderr)
            finalizer = (bundle / "scripts/post-deploy").read_text()
            self.assertIn('readonly APP_GROUP="example_runtime"', finalizer)
            self.assertIn('readonly COMMIT_OWNER="root:$APP_GROUP"', finalizer)
            self.assertIn('chown -R root:"$APP_GROUP"', finalizer)
            setup = (bundle / "setup").read_text()
            self.assertIn('readonly APP_GROUP="example_runtime"', setup)


if __name__ == "__main__":
    unittest.main()
