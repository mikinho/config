#!/usr/bin/env python3
"""Behavioral conformance tests for rendered deployment transactions."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import Final, Mapping, Sequence
import unittest


REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[1]
APPLICATION_ROOT: Final = REPOSITORY_ROOT / "deploy" / "application"
RENDERER: Final = APPLICATION_ROOT / "render_core.py"
PROFILE: Final = APPLICATION_ROOT / "profiles" / "example_node_app.json"
SOURCE_REVISION: Final = "0123456789abcdef0123456789abcdef01234567"
FIRST_TOKEN: Final = f"{'a' * 40}-1787531000-{'b' * 24}"
SECOND_TOKEN: Final = f"{'c' * 64}-1787531001-{'d' * 24}"
OLDER_TOKEN: Final = f"{'e' * 40}-1787530999-{'f' * 24}"
INVOCATION_ID: Final = "1" * 32
TRIGGER_TEST_ENV: Final = "EXAMPLE_NODE_APP_DEPLOY_TRIGGER_TESTING"
GATEWAY_TEST_ENV: Final = "EXAMPLE_NODE_APP_DEPLOY_GATEWAY_TESTING"
SNAPSHOT_TEST_ENV: Final = "EXAMPLE_NODE_APP_MANIFEST_SNAPSHOT_TESTING"
CLAIM_PATTERN: Final = r"^deploy-trigger-[0-9]+-[0-9a-f]{24}$"


def run(
    arguments: Sequence[str],
    *,
    environment: Mapping[str, str] | None = None,
    timeout: float = 10.0,
) -> subprocess.CompletedProcess[str]:
    """Run one bounded subprocess and return decoded output."""

    child_environment = os.environ.copy()
    if environment is not None:
        child_environment.update(environment)
    return subprocess.run(
        arguments,
        check=False,
        capture_output=True,
        text=True,
        env=child_environment,
        timeout=timeout,
    )


def render_bundle(parent: Path) -> Path:
    """Render one synthetic bundle below an isolated parent."""

    output = parent / "bundle"
    result = run(
        (
            sys.executable,
            str(RENDERER),
            "--profile",
            str(PROFILE),
            "--source-revision",
            SOURCE_REVISION,
            "--output",
            str(output),
        )
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return output


class RenderedBundleTestCase(unittest.TestCase):
    """Provide an independently rendered bundle for every behavioral test."""

    temporary: tempfile.TemporaryDirectory[str]
    root: Path
    bundle: Path

    def setUp(self) -> None:
        """Create one test-local rendered bundle."""

        self.temporary = tempfile.TemporaryDirectory(
            prefix="application-deployment-transaction-"
        )
        self.root = Path(self.temporary.name)
        self.bundle = render_bundle(self.root)

    def tearDown(self) -> None:
        """Remove the isolated bundle and state tree."""

        self.temporary.cleanup()


class GatewayContractTests(RenderedBundleTestCase):
    """Verify exact forced-command parsing and end-to-end acknowledgment."""

    def session_command(self, token: str, rsync_arguments: str | None = None) -> str:
        """Return the exact receiver command emitted by a conforming client."""

        receiver = rsync_arguments or (
            "--server -vOtrze.iLsfxCIvu --log-format=%i --delete "
            f"--fsync --super . {token}/"
        )
        return f"example-deploy-session {token} {receiver}"

    def test_gateway_parser_accepts_only_the_reviewed_receiver_shape(self) -> None:
        """Metadata, link, device, and arbitrary-option receiver requests fail."""

        helper = self.bundle / "scripts" / "deploy-gateway.py"
        valid = self.session_command(FIRST_TOKEN)
        invalid = (
            self.session_command(
                FIRST_TOKEN,
                "--server -vOtrze.iLsfxCIvu --log-format=%i --delete "
                f"--fsync --super --links . {FIRST_TOKEN}/",
            ),
            self.session_command(
                FIRST_TOKEN,
                "--server -vOtrze.iLsfxCIvu --log-format=%i --delete "
                f"--fsync --super --devices . {FIRST_TOKEN}/",
            ),
            f"example-deploy-session {FIRST_TOKEN} --server . .",
            "sh -c id",
        )
        program = """
import runpy
import sys

module = runpy.run_path(sys.argv[1])
parse = module["parse_original_command"]
gateway_error = module["GatewayError"]
parse(sys.argv[2])
for command in sys.argv[3:]:
    try:
        parse(command)
    except gateway_error:
        continue
    raise SystemExit("unsafe command was accepted")
"""
        result = run(
            (sys.executable, "-c", program, str(helper), valid, *invalid)
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def create_gateway_harness(self) -> tuple[Path, Path, Path]:
        """Create the guarded gateway test root and fake receiver programs."""

        root = self.root / "gateway"
        root.mkdir(mode=0o755)
        inbox_mode = 0o770 if sys.platform == "darwin" else 0o2770
        for name, mode in (
            ("inbox", inbox_mode),
            ("app", inbox_mode),
            ("results", 0o750),
        ):
            path = root / name
            path.mkdir(mode=mode)
            path.chmod(mode)
        for name in ("ingress.lock", "publish.lock", "session.lock"):
            path = root / name
            path.write_bytes(b"")
            path.chmod(0o660)

        fake_rrsync = root / "rrsync"
        fake_rrsync.write_text(
            """#!/usr/bin/env python3
import os
from pathlib import Path
import shlex
import sys

root = Path(os.environ.get("FAKE_RRSYNC_ROOT", sys.argv[-1]))
token = shlex.split(os.environ["SSH_ORIGINAL_COMMAND"])[-1].rstrip("/")
status = int(os.environ.get("FAKE_RRSYNC_STATUS", "0"))
if status:
    raise SystemExit(status)
(root / token).mkdir(mode=0o755)
print("wrapper=" + "|".join(sys.argv[1:]))
""",
            encoding="utf-8",
        )
        fake_rrsync.chmod(0o755)
        fake_sync = root / "sync"
        fake_sync.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake_sync.chmod(0o755)
        return root, root / "inbox" / ".deploy-done", root / "results"

    def test_gateway_waits_for_append_only_success_before_acknowledging(self) -> None:
        """A successful transfer remains blocked until root result evidence exists."""

        harness, sentinel, results = self.create_gateway_harness()
        helper = self.bundle / "scripts" / "deploy-gateway.py"
        environment = os.environ.copy()
        environment.update(
            {
                GATEWAY_TEST_ENV: "1",
                "SSH_ORIGINAL_COMMAND": self.session_command(FIRST_TOKEN),
            }
        )
        child = subprocess.Popen(
            (sys.executable, str(helper), "--test-root", str(harness)),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )
        deadline = time.monotonic() + 5.0
        while not sentinel.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(sentinel.exists(), "gateway did not publish a sentinel")
        self.assertIsNone(child.poll(), "gateway acknowledged before host result evidence")
        self.assertEqual(sentinel.read_text(encoding="utf-8").strip(), FIRST_TOKEN)

        sentinel.unlink()
        claim = f"deploy-trigger-1787531002000000000-{'e' * 24}"
        result_path = results / f"{FIRST_TOKEN}.{claim}"
        result_path.write_text("success\n", encoding="utf-8")
        result_path.chmod(0o640)
        stdout, stderr = child.communicate(timeout=5.0)
        self.assertEqual(child.returncode, 0, stderr)
        self.assertIn("-wo|-munge|-no-lock", stdout)

    def test_failed_receiver_retains_marker_and_blocks_new_payloads(self) -> None:
        """Interrupted transfers require explicit recovery before any later rsync."""

        harness, sentinel, _ = self.create_gateway_harness()
        helper = self.bundle / "scripts" / "deploy-gateway.py"
        first = run(
            (sys.executable, str(helper), "--test-root", str(harness)),
            environment={
                GATEWAY_TEST_ENV: "1",
                "SSH_ORIGINAL_COMMAND": self.session_command(FIRST_TOKEN),
                "FAKE_RRSYNC_STATUS": "7",
            },
        )
        self.assertEqual(first.returncode, 7, first.stderr)
        marker = harness / "inbox" / ".transfer-in-progress"
        self.assertEqual(marker.read_text(encoding="ascii"), f"{FIRST_TOKEN}\n")
        self.assertFalse(sentinel.exists())

        second = run(
            (sys.executable, str(helper), "--test-root", str(harness)),
            environment={
                GATEWAY_TEST_ENV: "1",
                "SSH_ORIGINAL_COMMAND": self.session_command(SECOND_TOKEN),
            },
        )
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("interrupted deploy transfer requires recovery", second.stderr)


class TriggerTransactionTests(RenderedBundleTestCase):
    """Exercise append-only claims, completion, quarantine, and recovery."""

    def create_trigger_harness(self) -> Path:
        """Create one trigger tree matching the helper's guarded test contract."""

        root = self.root / "trigger-root"
        root.mkdir(mode=0o755)
        root.chmod(0o755)
        trigger_mode = 0o770 if sys.platform == "darwin" else 0o2770
        for name, mode in (
            ("trigger", trigger_mode),
            ("state", 0o700),
            ("results", 0o750),
        ):
            path = root / name
            path.mkdir(mode=mode)
            path.chmod(mode)
        for name in ("ingress.lock", "publish.lock", "session.lock"):
            path = root / name
            path.write_bytes(b"")
            path.chmod(0o660)
        return root

    def helper(self, root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        """Run the rendered trigger helper in its guarded test mode."""

        return run(
            (
                sys.executable,
                str(self.bundle / "scripts" / "deploy-trigger.py"),
                "--test-root",
                str(root),
                *arguments,
            ),
            environment={TRIGGER_TEST_ENV: "1"},
        )

    def claim(self, root: Path, token: str = FIRST_TOKEN) -> str:
        """Publish and atomically claim one valid sentinel."""

        sentinel = root / "trigger" / ".deploy-done"
        sentinel.write_text(f"{token}\n", encoding="ascii")
        result = self.helper(root, "claim")
        self.assertEqual(result.returncode, 0, result.stderr)
        claim = result.stdout.strip()
        self.assertRegex(claim, CLAIM_PATTERN)
        return claim

    def test_success_is_append_only_and_bound_to_the_invocation(self) -> None:
        """Completion stages success, removes its claim, and preserves intent."""

        root = self.create_trigger_harness()
        claim = self.claim(root)
        result = self.helper(root, "complete", claim, INVOCATION_ID)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((root / "state" / claim).exists())
        evidence = root / "results" / f"{FIRST_TOKEN}.{claim}"
        self.assertEqual(evidence.read_bytes(), b"success\n")
        self.assertEqual(stat.S_IMODE(evidence.stat().st_mode), 0o640)
        self.assertEqual(
            (root / "completion-intent").read_text(encoding="ascii"),
            f"{INVOCATION_ID} {FIRST_TOKEN} {claim}\n",
        )
        self.assertFalse((root / "blocked").exists())

    def test_conflicting_terminal_evidence_is_never_replaced(self) -> None:
        """An existing failure result prevents a competing success transition."""

        root = self.create_trigger_harness()
        claim = self.claim(root)
        evidence = root / "results" / f"{FIRST_TOKEN}.{claim}"
        evidence.write_text("failed\n", encoding="ascii")
        evidence.chmod(0o640)
        original_inode = evidence.stat().st_ino
        result = self.helper(root, "complete", claim, INVOCATION_ID)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("conflicting append-only", result.stderr)
        self.assertEqual(evidence.stat().st_ino, original_inode)
        self.assertEqual(evidence.read_bytes(), b"failed\n")
        self.assertTrue((root / "state" / claim / "token").is_file())

    def test_invalid_hardlinked_sentinel_is_quarantined(self) -> None:
        """A deploy-controlled second hard link cannot become trusted evidence."""

        root = self.create_trigger_harness()
        sentinel = root / "trigger" / ".deploy-done"
        sentinel.write_text(f"{FIRST_TOKEN}\n", encoding="ascii")
        os.link(sentinel, root / "trigger" / "attacker-link")
        result = self.helper(root, "claim")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exactly one hard link", result.stderr)
        claims = tuple((root / "state").iterdir())
        self.assertEqual(len(claims), 1)
        self.assertTrue((claims[0] / "INVALID").is_file())
        self.assertFalse(sentinel.exists())

    def test_recovery_finishes_a_durable_completion_intent(self) -> None:
        """A reboot can publish success from exact retained invocation intent."""

        root = self.create_trigger_harness()
        claim = self.claim(root)
        intent = root / "completion-intent"
        intent.write_text(
            f"{INVOCATION_ID} {FIRST_TOKEN} {claim}\n", encoding="ascii"
        )
        intent.chmod(0o600)
        result = self.helper(root, "recover-completion", INVOCATION_ID)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), f"{FIRST_TOKEN} {claim}")
        self.assertFalse((root / "state" / claim).exists())
        self.assertEqual(
            (root / "results" / f"{FIRST_TOKEN}.{claim}").read_bytes(),
            b"success\n",
        )

    def test_pending_success_recovery_finishes_claim_transition(self) -> None:
        """A crash after staging success is completed without rewriting evidence."""

        root = self.create_trigger_harness()
        claim = self.claim(root)
        pending = root / "results" / f".pending-{FIRST_TOKEN}.{claim}"
        pending.write_text("success\n", encoding="ascii")
        pending.chmod(0o640)
        trigger_mode = 0o770 if sys.platform == "darwin" else 0o2770
        (root / "trigger").chmod(trigger_mode & ~0o020)
        result = self.helper(root, "recover-pending")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "1")
        self.assertFalse((root / "state" / claim).exists())
        self.assertFalse(pending.exists())
        self.assertEqual(
            (root / "results" / f"{FIRST_TOKEN}.{claim}").read_bytes(),
            b"success\n",
        )


class ManifestSnapshotTests(RenderedBundleTestCase):
    """Verify no-follow, one-link, all-or-nothing dependency snapshots."""

    def create_snapshot_harness(self) -> tuple[Path, Path, Path]:
        """Create a candidate, trusted work root, and fixed workspace."""

        root = self.root / "snapshot"
        root.mkdir(mode=0o700)
        root.chmod(0o700)
        candidate = root / FIRST_TOKEN
        candidate.mkdir(mode=0o755)
        candidate.chmod(0o755 if sys.platform == "darwin" else 0o2755)
        work_root = root / "npm-work"
        work_root.mkdir(mode=0o710)
        work_root.chmod(0o710)
        workspace = work_root / "install.A1b2C3"
        workspace.mkdir(mode=0o700)
        workspace.chmod(0o700)
        (candidate / "package.json").write_text(
            '{"name":"example","dependencies":{}}\n', encoding="utf-8"
        )
        (candidate / "package-lock.json").write_text(
            '{"lockfileVersion":3,"packages":{"":{}}}\n', encoding="utf-8"
        )
        return root, candidate, workspace

    def snapshot(
        self, root: Path, workspace: Path
    ) -> subprocess.CompletedProcess[str]:
        """Run the rendered snapshot helper in guarded test mode."""

        return run(
            (
                sys.executable,
                str(self.bundle / "scripts" / "snapshot-manifests.py"),
                "--test-root",
                str(root),
                FIRST_TOKEN,
                workspace.name,
            ),
            environment={SNAPSHOT_TEST_ENV: "1"},
        )

    def test_snapshot_is_exact_single_link_and_read_only(self) -> None:
        """Both manifest bytes are copied to new stable, protected inodes."""

        root, candidate, workspace = self.create_snapshot_harness()
        result = self.snapshot(root, workspace)
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in ("package-lock.json", "package.json"):
            source = candidate / name
            destination = workspace / name
            self.assertEqual(destination.read_bytes(), source.read_bytes())
            metadata = destination.stat()
            self.assertEqual(metadata.st_nlink, 1)
            self.assertEqual(stat.S_IMODE(metadata.st_mode), 0o400)

    def test_snapshot_rejects_symlink_and_hardlink_without_partial_output(self) -> None:
        """Alias attacks fail before either trusted snapshot remains."""

        for attack in ("symlink", "hardlink"):
            with self.subTest(attack=attack):
                root, candidate, workspace = self.create_snapshot_harness()
                lock = candidate / "package-lock.json"
                if attack == "symlink":
                    lock.unlink()
                    lock.symlink_to("package.json")
                else:
                    os.link(candidate / "package.json", candidate / "package.alias")
                result = self.snapshot(root, workspace)
                self.assertNotEqual(result.returncode, 0)
                for name in ("package-lock.json", "package.json"):
                    self.assertFalse((workspace / name).exists())
                shutil.rmtree(root)


class FinalizerRecoveryTests(RenderedBundleTestCase):
    """Exercise atomic activation, rollback, and crash recovery."""

    def create_release_layout(self) -> tuple[Path, Path]:
        """Create three immutable-release identities and current pointers."""

        root = self.root / "release-state"
        secure = self.root / "secure-state"
        (root / "releases").mkdir(parents=True)
        (root / "candidates").mkdir()
        (secure / "state").mkdir(parents=True)
        for token in (OLDER_TOKEN, FIRST_TOKEN, SECOND_TOKEN):
            build = root / "releases" / token / ".build"
            build.mkdir(parents=True)
            (build / "deploy.json").write_text(
                json.dumps({"schemaVersion": 1, "deployId": token}) + "\n",
                encoding="utf-8",
            )
        (root / "current").symlink_to(f"releases/{FIRST_TOKEN}")
        (root / "previous").symlink_to(f"releases/{OLDER_TOKEN}")
        return root, secure

    def patched_finalizer(self, root: Path, secure: Path) -> Path:
        """Bind fixed production roots to one test tree without weakening functions."""

        source = (self.bundle / "scripts" / "post-deploy").read_text(
            encoding="utf-8"
        )
        node = shutil.which("node")
        false = shutil.which("false")
        if node is None or false is None:
            self.skipTest("node and false are required")
        replacements = {
            'readonly APP_DIR="/opt/example_node_app"': f'readonly APP_DIR="{root}"',
            'readonly SECURE_DIR="/opt/.secure/example_node_app"': (
                f'readonly SECURE_DIR="{secure}"'
            ),
            "readonly NODE_BIN=/bin/node": f"readonly NODE_BIN={node}",
            "readonly SELINUX_ENABLED_BIN=/usr/sbin/selinuxenabled": (
                f"readonly SELINUX_ENABLED_BIN={false}"
            ),
            "readonly RESTORECON_BIN=/usr/sbin/restorecon": (
                f"readonly RESTORECON_BIN={false}"
            ),
        }
        for original, replacement in replacements.items():
            if original not in source:
                raise AssertionError(f"finalizer fixture marker missing: {original}")
            source = source.replace(original, replacement, 1)
        finalizer = self.root / "post-deploy-under-test"
        finalizer.write_text(source, encoding="utf-8")
        finalizer.chmod(0o700)
        return finalizer

    def shell(
        self,
        finalizer: Path,
        root: Path,
        program: str,
    ) -> subprocess.CompletedProcess[str]:
        """Source the patched finalizer and run one isolated state transition."""

        return run(
            ("bash", "-c", program),
            environment={
                "FINALIZER": str(finalizer),
                "TEST_ROOT": str(root),
                "OLD_TOKEN": FIRST_TOKEN,
                "NEW_TOKEN": SECOND_TOKEN,
                "OLDER_TOKEN": OLDER_TOKEN,
            },
        )

    def test_healthy_candidate_commits_and_preserves_previous(self) -> None:
        """A healthy token changes current only after durable activation intent."""

        root, secure = self.create_release_layout()
        finalizer = self.patched_finalizer(root, secure)
        result = self.shell(
            finalizer,
            root,
            """
source "$FINALIZER"
chown() { :; }
durably_sync_path() { :; }
systemctl() { printf '%s:%s\n' "$1" "$2" >> "$TEST_ROOT/systemctl.calls"; }
wait_for_health() { [[ "$2" == "$NEW_TOKEN" ]]; }
activate_release "$TEST_ROOT/current" "$TEST_ROOT/previous" example.service \\
    "releases/$NEW_TOKEN" "$NEW_TOKEN" "releases/$OLD_TOKEN" "$OLD_TOKEN"
""",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(os.readlink(root / "current"), f"releases/{SECOND_TOKEN}")
        self.assertEqual(os.readlink(root / "previous"), f"releases/{FIRST_TOKEN}")
        committed = json.loads((root / "committed.json").read_text())
        self.assertEqual(committed["deployId"], SECOND_TOKEN)
        self.assertFalse((secure / "state" / "activation.json").exists())

    def test_unhealthy_candidate_rolls_back_and_verifies_old_token(self) -> None:
        """Candidate failure restores current and proves the retained release."""

        root, secure = self.create_release_layout()
        finalizer = self.patched_finalizer(root, secure)
        result = self.shell(
            finalizer,
            root,
            """
source "$FINALIZER"
durably_sync_path() { :; }
systemctl() { printf '%s:%s\n' "$1" "$2" >> "$TEST_ROOT/systemctl.calls"; }
wait_for_health() { [[ "$2" == "$OLD_TOKEN" ]]; }
if activate_release "$TEST_ROOT/current" "$TEST_ROOT/previous" example.service \\
    "releases/$NEW_TOKEN" "$NEW_TOKEN" "releases/$OLD_TOKEN" "$OLD_TOKEN"; then
    exit 70
fi
[[ "$ROLLED_BACK" == 1 ]]
""",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(os.readlink(root / "current"), f"releases/{FIRST_TOKEN}")
        self.assertEqual(os.readlink(root / "previous"), f"releases/{OLDER_TOKEN}")
        calls = (root / "systemctl.calls").read_text()
        self.assertIn("reset-failed:example.service", calls)

    def test_interrupted_switch_recovers_but_divergence_fails_closed(self) -> None:
        """Recorded intent restores exact rollback state and rejects pointer drift."""

        root, secure = self.create_release_layout()
        finalizer = self.patched_finalizer(root, secure)
        interrupted = self.shell(
            finalizer,
            root,
            """
source "$FINALIZER"
durably_sync_path() { :; }
persist_activation_intent "releases/$NEW_TOKEN" "$NEW_TOKEN" \\
    "releases/$OLD_TOKEN" "$OLD_TOKEN"
atomic_set_link "$TEST_ROOT/current" "releases/$NEW_TOKEN"
""",
        )
        self.assertEqual(interrupted.returncode, 0, interrupted.stderr)
        recovered = self.shell(
            finalizer,
            root,
            """
source "$FINALIZER"
durably_sync_path() { :; }
systemctl() { :; }
wait_for_health() { [[ "$2" == "$OLD_TOKEN" ]]; }
recover_incomplete_activation 1
""",
        )
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual(os.readlink(root / "current"), f"releases/{FIRST_TOKEN}")
        self.assertFalse((secure / "state" / "activation.json").exists())

        persisted = self.shell(
            finalizer,
            root,
            """
source "$FINALIZER"
durably_sync_path() { :; }
persist_activation_intent "releases/$NEW_TOKEN" "$NEW_TOKEN" \\
    "releases/$OLD_TOKEN" "$OLD_TOKEN"
atomic_set_link "$TEST_ROOT/current" "releases/$OLDER_TOKEN"
""",
        )
        self.assertEqual(persisted.returncode, 0, persisted.stderr)
        divergent = self.shell(
            finalizer,
            root,
            """
source "$FINALIZER"
durably_sync_path() { :; }
systemctl() { :; }
wait_for_health() { return 0; }
if recover_incomplete_activation 1; then
    exit 70
fi
""",
        )
        self.assertEqual(divergent.returncode, 0, divergent.stderr)
        self.assertEqual(os.readlink(root / "current"), f"releases/{OLDER_TOKEN}")
        self.assertTrue((secure / "state" / "activation.json").is_file())

    def test_first_release_failure_removes_pointer_and_stops_service(self) -> None:
        """An unhealthy initial release cannot remain selected without rollback."""

        root, secure = self.create_release_layout()
        (root / "current").unlink()
        (root / "previous").unlink()
        finalizer = self.patched_finalizer(root, secure)
        result = self.shell(
            finalizer,
            root,
            """
source "$FINALIZER"
durably_sync_path() { :; }
systemctl() { printf '%s:%s\n' "$1" "$2" >> "$TEST_ROOT/systemctl.calls"; }
wait_for_health() { return 1; }
if activate_release "$TEST_ROOT/current" "$TEST_ROOT/previous" example.service \\
    "releases/$NEW_TOKEN" "$NEW_TOKEN" "" ""; then
    exit 70
fi
""",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((root / "current").exists())
        self.assertFalse((root / "current").is_symlink())
        self.assertIn("stop:example.service", (root / "systemctl.calls").read_text())

    def test_failed_isolated_preflight_never_changes_current(self) -> None:
        """Candidate health fails before the live release pointer is touched."""

        root, secure = self.create_release_layout()
        finalizer = self.patched_finalizer(root, secure)
        result = self.shell(
            finalizer,
            root,
            """
source "$FINALIZER"
systemd-escape() { printf 'example_node_app-candidate@%s.service\n' "$2"; }
systemctl() { :; }
wait_for_health() { return 1; }
if preflight_release "$NEW_TOKEN"; then
    exit 70
fi
""",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(os.readlink(root / "current"), f"releases/{FIRST_TOKEN}")
        self.assertEqual(tuple((root / "candidates").iterdir()), ())

    def test_promotion_sync_failure_cannot_mutate_current(self) -> None:
        """Release durability failure aborts before any activation intent or switch."""

        root, secure = self.create_release_layout()
        candidate = root / "incoming" / SECOND_TOKEN
        candidate.mkdir(parents=True)
        release = root / "releases" / SECOND_TOKEN
        shutil.rmtree(release)
        finalizer = self.patched_finalizer(root, secure)
        result = self.shell(
            finalizer,
            root,
            f"""
source "$FINALIZER"
DEPLOY_TOKEN="$NEW_TOKEN"
CANDIDATE_DIR={candidate!s}
RELEASE_DIR={release!s}
relabel() {{ :; }}
durably_sync_release() {{ return 73; }}
promote_candidate
""",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("promoted release was not durable", result.stderr)
        self.assertEqual(os.readlink(root / "current"), f"releases/{FIRST_TOKEN}")

    def test_committed_acknowledgment_follows_activation_intent_clear(self) -> None:
        """A client-visible commit cannot be published while rollback intent remains."""

        root, secure = self.create_release_layout()
        finalizer = self.patched_finalizer(root, secure)
        result = self.shell(
            finalizer,
            root,
            """
source "$FINALIZER"
durably_sync_path() { :; }
systemctl() { :; }
wait_for_health() { return 0; }
clear_activation_intent() { printf 'clear\n' >> "$TEST_ROOT/order"; }
publish_committed_deploy() { printf 'publish\n' >> "$TEST_ROOT/order"; }
activate_release "$TEST_ROOT/current" "$TEST_ROOT/previous" example.service \\
    "releases/$NEW_TOKEN" "$NEW_TOKEN" "releases/$OLD_TOKEN" "$OLD_TOKEN"
""",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((root / "order").read_text().splitlines(), ["clear", "publish"])


if __name__ == "__main__":
    unittest.main()
