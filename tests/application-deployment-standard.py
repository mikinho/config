#!/usr/bin/env python3
"""Focused hostile-input tests for gold deployment project profiles."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
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
        self, output: Path, revision: str = SOURCE_REVISION
    ) -> subprocess.CompletedProcess[str]:
        """Run the public renderer against the synthetic example profile."""

        return subprocess.run(
            [
                sys.executable,
                str(RENDERER_PATH),
                "--profile",
                str(EXAMPLE_PROFILE),
                "--source-revision",
                revision,
                "--output",
                str(output),
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


if __name__ == "__main__":
    unittest.main()
