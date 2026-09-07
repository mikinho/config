#!/usr/bin/env python3

#
# Author: Michael Welter <me@mikinho.com> - https://github.com/mikinho
#

"""Verify issuance plans and configuration guards against Certbot's real parser.

Run with a Python environment containing Certbot and its matching ACME package.
Only parser construction is exercised: no Certbot command handler, CA request,
hook execution, host configuration, or system service is used. Temporary files
replace both default cli.ini sources and every Certbot state directory.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Final
from unittest import mock

sys.dont_write_bytecode = True

try:
    from certbot._internal import cli, constants
    from certbot._internal.plugins import disco
    from certbot.configuration import NamespaceConfig
except ImportError as error:
    raise SystemExit(
        "certbot-parser requires Certbot and its matching ACME package in the selected Python environment"
    ) from error


REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[1]
ISSUER: Final = REPOSITORY_ROOT / "certbot" / "issue"
PRODUCTION_SERVER: Final = "https://acme-v02.api.letsencrypt.org/directory"
STAGING_SERVER: Final = "https://acme-staging-v02.api.letsencrypt.org/directory"
STATE_DIRECTORIES: Final = {
    "--config-dir": "/etc/letsencrypt",
    "--work-dir": "/var/lib/letsencrypt",
    "--logs-dir": "/var/log/letsencrypt",
}
PARSE_LOOP: Final = '\nwhile [ "$#" -gt 0 ]; do\n'
TESTING_DIRECTIVES: Final = (
    "staging = true",
    "test-cert: yes",
    "--staging",
    "  --test-cert = on",
    "dry-run = true",
    "--dry-run: 1",
    "\u00a0staging = true",
    "server = https://example.invalid/directory\rstaging = true",
    "# comment\rstaging = true",
    "; comment\r--test-cert\r",
    "server = https://example.invalid/directory\r\ndry-run = true",
)


def planned_commands(environment: str) -> list[list[str]]:
    """Read the helper's own planned argument vectors without probing the host."""

    arguments = [
        str(ISSUER),
        "--plan",
        f"--{environment}",
        "--email",
        "ops@example.com",
        "--domain",
        "example.com",
    ]
    if environment == "production":
        arguments.append("--staging-passed")
    result = subprocess.run(
        arguments, text=True, capture_output=True, check=True, timeout=10
    )
    return [
        shlex.split(line)[2:]
        for line in result.stdout.splitlines()
        if line.startswith("+ /bin/certbot ")
    ]


class CertbotParserTests(unittest.TestCase):
    """Exercise real option precedence while keeping all runtime effects blocked."""

    def setUp(self) -> None:
        """Allocate isolated config sources and load only the helper's functions."""

        temporary = tempfile.TemporaryDirectory(prefix="config-certbot-parser-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.system_config = self.root / "system-cli.ini"
        self.user_config = self.root / "user-cli.ini"
        self.functions = self.root / "issuer-functions"
        source = ISSUER.read_text(encoding="utf-8")
        self.assertEqual(
            source.count(PARSE_LOOP),
            1,
            "cannot isolate the issuer function definitions",
        )
        self.functions.write_text(source.split(PARSE_LOOP, 1)[0], encoding="utf-8")
        self.plugins = disco.PluginsRegistry.find_all()

    def parse(self, arguments: list[str]) -> NamespaceConfig:
        """Parse an actual plan with fixture-only config files and storage paths."""

        isolated_arguments = arguments.copy()
        for option, expected_directory in STATE_DIRECTORIES.items():
            index = isolated_arguments.index(option) + 1
            self.assertEqual(isolated_arguments[index], expected_directory)
            isolated_arguments[index] = str(self.root / option.removeprefix("--"))
        if "--webroot-path" in isolated_arguments:
            index = isolated_arguments.index("--webroot-path") + 1
            self.assertEqual(isolated_arguments[index], "/var/www/letsencrypt")
            webroot = self.root / "webroot"
            webroot.mkdir(exist_ok=True)
            isolated_arguments[index] = str(webroot)
        with (
            mock.patch.dict(
                constants.CLI_DEFAULTS,
                {
                    "config_files": [str(self.system_config), str(self.user_config)],
                },
            ),
            mock.patch(
                "socket.socket.connect",
                side_effect=AssertionError("unexpected network access"),
            ),
        ):
            return cli.prepare_and_parse_args(self.plugins, isolated_arguments)

    def guard(self, config_path: Path) -> subprocess.CompletedProcess[str]:
        """Call the production guard on one fixture without executing Certbot."""

        return subprocess.run(
            [
                "sh",
                "-c",
                '. "$1"; validate_inherited_config_file "$2"',
                "certbot-parser-test",
                str(self.functions),
                str(config_path),
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )

    def test_benign_defaults_preserve_all_planned_modes(self) -> None:
        """CLI endpoints and state paths win without discarding ordinary defaults."""

        self.system_config.write_text(
            f"server = {STAGING_SERVER}\r\nrsa-key-size = 3072\r\n",
            encoding="utf-8",
        )
        self.user_config.write_text(
            "server = https://acme.example.invalid/directory\n"
            "config-dir = /not-the-managed-config\nwork-dir = /not-the-managed-work\n"
            "logs-dir = /not-the-managed-logs\n",
            encoding="utf-8",
        )
        self.assertEqual(self.guard(self.system_config).returncode, 0)
        self.assertEqual(self.guard(self.user_config).returncode, 0)
        cases = (
            (planned_commands("staging")[0], STAGING_SERVER, True),
            (planned_commands("production")[0], PRODUCTION_SERVER, False),
            (planned_commands("production")[1], STAGING_SERVER, True),
        )
        for arguments, server, dry_run in cases:
            with self.subTest(command=arguments[0], server=server, dry_run=dry_run):
                parsed = self.parse(arguments)
                self.assertEqual(parsed.server, server)
                self.assertEqual(parsed.dry_run, dry_run)
                self.assertEqual(parsed.rsa_key_size, 3072)
                self.assertEqual(parsed.config_dir, str(self.root / "config-dir"))

    def test_guard_blocks_real_parser_testing_mode_overrides(self) -> None:
        """Both inherited sources can change production mode, and both are rejected."""

        arguments = planned_commands("production")[0]
        for config_path in (self.system_config, self.user_config):
            for directive in TESTING_DIRECTIVES:
                with self.subTest(source=config_path.name, directive=directive):
                    config_path.write_text(directive + "\n", encoding="utf-8")
                    parsed = self.parse(arguments)
                    self.assertEqual(parsed.server, STAGING_SERVER)
                    guarded = self.guard(config_path)
                    self.assertNotEqual(guarded.returncode, 0)
                    self.assertIn("inherited Certbot configuration", guarded.stderr)
            config_path.unlink()

    def test_empty_explicit_config_does_not_isolate_defaults(self) -> None:
        """Prevent replacing the guard with the ineffective --config /dev/null pattern."""

        self.system_config.write_text("staging = true\n", encoding="utf-8")
        empty_config = self.root / "empty.ini"
        empty_config.touch()
        parsed = self.parse(
            planned_commands("production")[0] + ["--config", str(empty_config)]
        )
        self.assertEqual(parsed.server, STAGING_SERVER)
        self.assertNotEqual(self.guard(self.system_config).returncode, 0)

    def test_false_alias_cannot_make_global_testing_controls_safe(self) -> None:
        """A higher-priority false alias does not neutralize every true default."""

        self.system_config.write_text("test-cert = true\n", encoding="utf-8")
        self.user_config.write_text("staging = false\n", encoding="utf-8")
        parsed = self.parse(planned_commands("production")[0])
        self.assertEqual(parsed.server, STAGING_SERVER)
        self.assertNotEqual(self.guard(self.system_config).returncode, 0)
        self.assertNotEqual(self.guard(self.user_config).returncode, 0)

    def test_missing_user_source_requires_trusted_ancestry(self) -> None:
        """A user must not be able to add a hook after an absent-default check."""

        self.assertFalse(self.user_config.exists())
        # Model an unprivileged existing ancestor independently of the runner's
        # UID. The production helper still inspects real file/path existence.
        guarded = subprocess.run(
            [
                "sh",
                "-c",
                '. "$1"; UNTRUSTED_ROOT=$3; '
                'path_identity() { case "$1" in '
                '"$UNTRUSTED_ROOT") printf "1000:700\\n" ;; '
                '*) printf "0:755\\n" ;; esac; }; '
                'require_trusted_configuration_source "$2"',
                "certbot-parser-test",
                str(self.functions),
                str(self.user_config),
                str(self.root),
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
        self.assertEqual(guarded.returncode, 1)
        self.assertIn("configuration directory must be owned by root", guarded.stderr)

        # Demonstrate the parser boundary the guard protects. Construction of
        # the parser accepts a newly created hook; no command handler runs it.
        self.user_config.write_text("pre-hook = /usr/bin/true\n", encoding="utf-8")
        parsed = self.parse(planned_commands("production")[0])
        self.assertIn("/usr/bin/true", parsed.pre_hook)

    def test_user_source_resolution_matches_certbot(self) -> None:
        """Check actual HOME/XDG discovery without opening any host cli.ini file."""

        fixture_home = self.root / "home"
        fixture_xdg = self.root / "xdg with spaces"
        for xdg in (None, str(fixture_xdg)):
            with self.subTest(xdg=xdg):
                environment = {
                    "PATH": os.defpath,
                    "HOME": str(fixture_home),
                    "PYTHONDONTWRITEBYTECODE": "1",
                }
                expected_directory = (
                    fixture_home / ".config" if xdg is None else fixture_xdg
                )
                if xdg is not None:
                    environment["XDG_CONFIG_HOME"] = xdg
                resolved = subprocess.run(
                    [
                        "sh",
                        "-c",
                        '. "$1"; resolve_user_config; printf "%s\\n" "$USER_CERTBOT_CONFIG"',
                        "certbot-parser-test",
                        str(self.functions),
                    ],
                    env=environment,
                    text=True,
                    capture_output=True,
                    check=True,
                    timeout=10,
                )
                expected_config = expected_directory / "letsencrypt" / "cli.ini"
                self.assertEqual(resolved.stdout.strip(), str(expected_config))
                # Import constants in a fresh process: XDG_CONFIG_HOME is captured
                # at import time by Certbot, not when prepare_and_parse_args runs.
                discovered = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        (
                            "import json, os; from certbot._internal import constants; "
                            "print(json.dumps([os.path.expanduser(p) for p in constants.CLI_DEFAULTS['config_files']]))"
                        ),
                    ],
                    env=environment,
                    text=True,
                    capture_output=True,
                    check=True,
                    timeout=10,
                )
                self.assertEqual(
                    json.loads(discovered.stdout),
                    [
                        "/etc/letsencrypt/cli.ini",
                        str(expected_config),
                    ],
                )
                expected_config.parent.mkdir(parents=True, exist_ok=True)
                expected_config.write_text("staging = true\n", encoding="utf-8")
                guarded = subprocess.run(
                    [
                        "sh",
                        "-c",
                        '. "$1"; require_trusted_configuration_source() { :; }; '
                        "SYSTEM_CERTBOT_CONFIG=$2; validate_inherited_config",
                        "certbot-parser-test",
                        str(self.functions),
                        str(self.system_config),
                    ],
                    env=environment,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=10,
                )
                self.assertNotEqual(guarded.returncode, 0)
                self.assertIn(str(expected_config), guarded.stderr)

    def test_ambiguous_user_config_paths_fail_before_discovery(self) -> None:
        """Reject empty, relative, and expanding XDG paths without reading them."""

        for xdg in ("", "relative", "/tmp/*", "/tmp/?", "/tmp/[ab]", "~/config"):
            with self.subTest(xdg=xdg):
                result = subprocess.run(
                    [
                        "sh",
                        "-c",
                        '. "$1"; resolve_user_config',
                        "certbot-parser-test",
                        str(self.functions),
                    ],
                    env={
                        "PATH": os.defpath,
                        "HOME": str(self.root / "home"),
                        "XDG_CONFIG_HOME": xdg,
                    },
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=10,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Certbot user configuration directory", result.stderr)


if __name__ == "__main__":
    print(f"Testing the actual Certbot {importlib.metadata.version('certbot')} parser.")
    unittest.main()
