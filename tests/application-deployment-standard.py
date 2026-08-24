#!/usr/bin/env python3
"""Focused hostile-input tests for gold deployment project profiles."""

from __future__ import annotations

import copy
import importlib.util
import json
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

        source_path = PROFILE_ROOT / "example_node_app.json"
        self.source: dict[str, Any] = json.loads(source_path.read_text(encoding="utf-8"))

    def assert_invalid(self, source: dict[str, Any], message: str) -> None:
        """Assert that one decoded profile fails with a stable message fragment."""

        with self.assertRaisesRegex(VALIDATOR.ProfileError, message):
            VALIDATOR.validate_profile(source)

    def test_public_example_profile_validates(self) -> None:
        """The synthetic public profile conforms to the complete schema."""

        profile = VALIDATOR.load_profile(PROFILE_ROOT / "example_node_app.json")
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


if __name__ == "__main__":
    unittest.main()
