import json
import hashlib
import tarfile
import unittest
from io import StringIO
from pathlib import Path
import tempfile
from unittest.mock import MagicMock
from unittest.mock import patch

from ssh_remote_control import remote_cli
from ssh_remote_control.models import SSHProfile
from ssh_remote_control.profile_store import ProfileStore


class RemoteCliParserTests(unittest.TestCase):
    def test_launch_parser_keeps_local_options_and_forwards_remaining_codex_args(self) -> None:
        parser = remote_cli._build_parser()

        args, extra = parser.parse_known_args(
            ["launch", "prod", "--local-port", "4815", "--", "--help"]
        )

        self.assertEqual(args.command, "launch")
        self.assertEqual(args.profile_name, "prod")
        self.assertEqual(args.local_port, 4815)
        self.assertEqual(extra, ["--", "--help"])

    def test_profile_list_parser_accepts_json(self) -> None:
        parser = remote_cli._build_parser()

        args, extra = parser.parse_known_args(["profile", "list", "--json"])

        self.assertEqual(args.command, "profile")
        self.assertEqual(args.profile_command, "list")
        self.assertTrue(args.json)
        self.assertEqual(extra, [])

    def test_profile_current_parser_accepts_json(self) -> None:
        parser = remote_cli._build_parser()

        args, extra = parser.parse_known_args(["profile", "current", "--json"])

        self.assertEqual(args.command, "profile")
        self.assertEqual(args.profile_command, "current")
        self.assertTrue(args.json)
        self.assertEqual(extra, [])

    def test_profile_aliases_parser_accepts_json_and_config_path(self) -> None:
        parser = remote_cli._build_parser()

        args, extra = parser.parse_known_args(
            ["profile", "aliases", "--ssh-config-path", "/tmp/ssh-config", "--all", "--json"]
        )

        self.assertEqual(args.command, "profile")
        self.assertEqual(args.profile_command, "aliases")
        self.assertEqual(args.ssh_config_path, "/tmp/ssh-config")
        self.assertTrue(args.all)
        self.assertTrue(args.json)
        self.assertEqual(extra, [])

    def test_profile_show_parser_accepts_name_and_json(self) -> None:
        parser = remote_cli._build_parser()

        args, extra = parser.parse_known_args(["profile", "show", "prod", "--json"])

        self.assertEqual(args.command, "profile")
        self.assertEqual(args.profile_command, "show")
        self.assertEqual(args.name, "prod")
        self.assertTrue(args.json)
        self.assertEqual(extra, [])

    def test_profile_doctor_parser_accepts_json_and_rewrite(self) -> None:
        parser = remote_cli._build_parser()

        args, extra = parser.parse_known_args(["profile", "doctor", "--json", "--rewrite"])

        self.assertEqual(args.command, "profile")
        self.assertEqual(args.profile_command, "doctor")
        self.assertTrue(args.json)
        self.assertTrue(args.rewrite)
        self.assertEqual(extra, [])

    def test_profile_delete_parser_accepts_name(self) -> None:
        parser = remote_cli._build_parser()

        args, extra = parser.parse_known_args(["profile", "delete", "prod"])

        self.assertEqual(args.command, "profile")
        self.assertEqual(args.profile_command, "delete")
        self.assertEqual(args.name, "prod")
        self.assertEqual(extra, [])

    def test_profile_use_parser_accepts_name(self) -> None:
        parser = remote_cli._build_parser()

        args, extra = parser.parse_known_args(["profile", "use", "prod"])

        self.assertEqual(args.command, "profile")
        self.assertEqual(args.profile_command, "use")
        self.assertEqual(args.name, "prod")
        self.assertEqual(extra, [])

    def test_profile_import_ssh_config_parser_accepts_alias_and_defaults(self) -> None:
        parser = remote_cli._build_parser()

        args, extra = parser.parse_known_args(
            [
                "profile",
                "import-ssh-config",
                "prod",
                "--alias",
                "llm.ezbuilder.app",
                "--remote-root",
                "/tmp",
                "--service-scope",
                "user",
                "--default-model",
                "gpt-5.4",
            ]
        )

        self.assertEqual(args.command, "profile")
        self.assertEqual(args.profile_command, "import-ssh-config")
        self.assertEqual(args.name, "prod")
        self.assertEqual(args.alias, "llm.ezbuilder.app")
        self.assertEqual(args.remote_root, "/tmp")
        self.assertEqual(args.service_scope, "user")
        self.assertEqual(args.default_model, "gpt-5.4")
        self.assertEqual(extra, [])

    def test_smoke_parser_accepts_optional_local_port(self) -> None:
        parser = remote_cli._build_parser()

        args, extra = parser.parse_known_args(["smoke", "prod", "--local-port", "4900"])

        self.assertEqual(args.command, "smoke")
        self.assertEqual(args.profile_name, "prod")
        self.assertEqual(args.local_port, 4900)
        self.assertEqual(extra, [])

    def test_up_parser_accepts_optional_local_port(self) -> None:
        parser = remote_cli._build_parser()

        args, extra = parser.parse_known_args(["up", "prod", "--local-port", "4902", "--sync-local"])

        self.assertEqual(args.command, "up")
        self.assertEqual(args.profile_name, "prod")
        self.assertEqual(args.local_port, 4902)
        self.assertTrue(args.sync_local)
        self.assertEqual(extra, [])

    def test_up_parser_accepts_json_and_strict(self) -> None:
        parser = remote_cli._build_parser()

        args, extra = parser.parse_known_args(["up", "--json", "--strict"])

        self.assertEqual(args.command, "up")
        self.assertTrue(args.json)
        self.assertTrue(args.strict)
        self.assertIsNone(args.profile_name)
        self.assertEqual(extra, [])

    def test_up_parser_accepts_launch_and_forwarded_codex_args(self) -> None:
        parser = remote_cli._build_parser()

        args, extra = parser.parse_known_args(
            ["up", "prod", "--launch", "--", "-m", "gpt-5.4"]
        )

        self.assertEqual(args.command, "up")
        self.assertTrue(args.launch)
        self.assertEqual(extra, ["--", "-m", "gpt-5.4"])

    def test_doctor_parser_accepts_optional_local_port(self) -> None:
        parser = remote_cli._build_parser()

        args, extra = parser.parse_known_args(["doctor", "prod", "--local-port", "4901"])

        self.assertEqual(args.command, "doctor")
        self.assertEqual(args.profile_name, "prod")
        self.assertEqual(args.local_port, 4901)
        self.assertEqual(extra, [])

    def test_doctor_parser_accepts_json_and_strict(self) -> None:
        parser = remote_cli._build_parser()

        args, extra = parser.parse_known_args(["doctor", "--json", "--strict"])

        self.assertEqual(args.command, "doctor")
        self.assertTrue(args.json)
        self.assertTrue(args.strict)
        self.assertIsNone(args.profile_name)
        self.assertEqual(extra, [])

    def test_bootstrap_parser_accepts_profile_name(self) -> None:
        parser = remote_cli._build_parser()

        args, extra = parser.parse_known_args(["bootstrap", "prod"])

        self.assertEqual(args.command, "bootstrap")
        self.assertEqual(args.profile_name, "prod")
        self.assertEqual(extra, [])

    def test_logs_parser_accepts_line_count(self) -> None:
        parser = remote_cli._build_parser()

        args, extra = parser.parse_known_args(["logs", "prod", "--lines", "50"])

        self.assertEqual(args.command, "logs")
        self.assertEqual(args.profile_name, "prod")
        self.assertEqual(args.lines, 50)
        self.assertEqual(extra, [])

    def test_install_cli_parser_accepts_bin_dir(self) -> None:
        parser = remote_cli._build_parser()

        args, extra = parser.parse_known_args(
            ["install-cli", "--bin-dir", "/Users/ezbuilder/.local/bin"]
        )

        self.assertEqual(args.command, "install-cli")
        self.assertEqual(args.bin_dir, "/Users/ezbuilder/.local/bin")
        self.assertEqual(extra, [])

    def test_completion_parser_accepts_shell(self) -> None:
        parser = remote_cli._build_parser()

        args, extra = parser.parse_known_args(["completion", "zsh"])

        self.assertEqual(args.command, "completion")
        self.assertEqual(args.shell, "zsh")
        self.assertEqual(extra, [])

    def test_support_bundle_parser_accepts_options(self) -> None:
        parser = remote_cli._build_parser()

        args, extra = parser.parse_known_args(
            [
                "support-bundle",
                "prod",
                "--local-port",
                "4906",
                "--lines",
                "50",
                "--output",
                "/tmp/bundle.tar.gz",
                "--no-redact",
                "--json",
            ]
        )

        self.assertEqual(args.command, "support-bundle")
        self.assertEqual(args.profile_name, "prod")
        self.assertEqual(args.local_port, 4906)
        self.assertEqual(args.lines, 50)
        self.assertEqual(args.output, "/tmp/bundle.tar.gz")
        self.assertTrue(args.no_redact)
        self.assertTrue(args.json)
        self.assertEqual(extra, [])

    def test_package_parser_accepts_json_and_skip_flags(self) -> None:
        parser = remote_cli._build_parser()

        args, extra = parser.parse_known_args(
            ["package", "--json", "--skip-tests", "--skip-build", "--skip-bundle", "--no-clean", "--strict-release"]
        )

        self.assertEqual(args.command, "package")
        self.assertTrue(args.json)
        self.assertTrue(args.skip_tests)
        self.assertTrue(args.skip_build)
        self.assertTrue(args.skip_bundle)
        self.assertTrue(args.no_clean)
        self.assertTrue(args.strict_release)
        self.assertEqual(extra, [])

    def test_open_parser_accepts_launch_options(self) -> None:
        parser = remote_cli._build_parser()

        args, extra = parser.parse_known_args(["open", "prod", "--local-port", "4908", "--", "continue"])

        self.assertEqual(args.command, "open")
        self.assertEqual(args.profile_name, "prod")
        self.assertEqual(args.local_port, 4908)
        self.assertEqual(extra, ["--", "continue"])

    @patch("ssh_remote_control.remote_cli.subprocess.run")
    def test_package_json_runs_release_steps_and_emits_summary(self, run_mock: MagicMock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='tmp'\nversion='0.0.0'\n")
            scripts_dir = root / "scripts"
            scripts_dir.mkdir()
            (scripts_dir / "build_plugin_bundle.py").write_text("print('ok')\n")
            dist_dir = root / "dist"
            dist_dir.mkdir()
            wheel_path = dist_dir / "ssh_remote_control-0.1.0-py3-none-any.whl"
            sdist_path = dist_dir / "ssh_remote_control-0.1.0.tar.gz"
            bundle_path = dist_dir / "ssh-remote-control-plugin.zip"
            wheel_path.write_text("wheel")
            sdist_path.write_text("sdist")
            bundle_path.write_text("bundle")

            run_mock.side_effect = [
                MagicMock(returncode=0, stdout="", stderr=""),
                MagicMock(returncode=0, stdout="", stderr=""),
                MagicMock(
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "status": "ok",
                            "bundle": str(bundle_path),
                            "bundle_root": "ssh-remote-control",
                            "files": ["README.md"],
                        }
                    ),
                    stderr="",
                ),
            ]

            with patch.object(remote_cli, "_project_root", return_value=root):
                with patch("sys.stdout", new_callable=StringIO) as stdout:
                    exit_code = remote_cli.main(["package", "--json", "--no-clean"])

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["tests"]["status"], "ok")
        self.assertEqual(payload["build"]["status"], "ok")
        self.assertEqual(payload["bundle"]["bundle"], str(bundle_path))
        self.assertTrue(payload["checksums"].endswith("SHA256SUMS"))
        self.assertTrue(payload["manifest"].endswith("release-manifest.json"))
        commands = [call.args[0] for call in run_mock.call_args_list]
        self.assertEqual(commands[0][:6], [remote_cli.sys.executable, "-m", "unittest", "discover", "-s", "tests"])
        self.assertEqual(commands[1][-2:], ["-m", "build"])
        self.assertIn("scripts/build_plugin_bundle.py", commands[2][1])

    def test_write_release_metadata_creates_checksums_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dist_dir = Path(tmp)
            wheel_path = dist_dir / "ssh_remote_control-0.1.0-py3-none-any.whl"
            bundle_path = dist_dir / "ssh-remote-control-plugin.zip"
            wheel_path.write_text("wheel-bytes")
            bundle_path.write_text("bundle-bytes")

            result = remote_cli._write_release_metadata(
                dist_dir,
                [wheel_path, bundle_path],
                context={
                    "generated_at_utc": "2026-04-11T00:00:00Z",
                    "project": {"name": "ssh-remote-control", "version": "0.1.0"},
                    "git": {"commit": "abc123", "dirty": False},
                    "builder": {
                        "python_version": "3.11.9",
                        "python_executable": "/tmp/python",
                        "platform": "darwin",
                    },
                    "validation": {
                        "warnings": [],
                    },
                },
            )

            checksums_path = dist_dir / "SHA256SUMS"
            manifest_path = dist_dir / "release-manifest.json"
            self.assertEqual(result["checksums"], str(checksums_path))
            self.assertEqual(result["manifest"], str(manifest_path))
            manifest = json.loads(manifest_path.read_text())
            self.assertEqual(manifest["status"], "ok")
            self.assertEqual(manifest["generated_at_utc"], "2026-04-11T00:00:00Z")
            self.assertEqual(manifest["project"]["version"], "0.1.0")
            self.assertEqual(manifest["git"]["commit"], "abc123")
            self.assertEqual(manifest["builder"]["platform"], "darwin")
            self.assertEqual(manifest["validation"]["warnings"], [])
            self.assertEqual(
                [artifact["name"] for artifact in manifest["artifacts"]],
                [wheel_path.name, bundle_path.name],
            )
            self.assertEqual(
                manifest["artifacts"][0]["sha256"],
                hashlib.sha256(b"wheel-bytes").hexdigest(),
            )
            self.assertIn(
                f"{hashlib.sha256(b'bundle-bytes').hexdigest()}  {bundle_path.name}",
                checksums_path.read_text(),
            )

    def test_completion_command_renders_shell_script(self) -> None:
        with patch("sys.stdout", new_callable=StringIO) as stdout:
            exit_code = remote_cli.main(["completion", "bash"])

        self.assertEqual(exit_code, 0)
        self.assertIn("complete -F _codex_remote_complete codex-remote", stdout.getvalue())

    @patch("ssh_remote_control.remote_cli.subprocess.run")
    def test_collect_release_context_reads_project_version_and_git_state(
        self,
        run_mock: MagicMock,
    ) -> None:
        run_mock.side_effect = [
            MagicMock(returncode=0, stdout="abc123\n", stderr=""),
            MagicMock(returncode=0, stdout=" M README.md\n", stderr=""),
        ]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "pyproject.toml").write_text(
                "[project]\nname = 'ssh-remote-control'\nversion = '0.1.0'\n"
            )
            plugin_dir = root / ".codex-plugin"
            plugin_dir.mkdir()
            (plugin_dir / "plugin.json").write_text(
                '{"name":"ssh-remote-control","version":"0.1.0"}'
            )

            context = remote_cli._collect_release_context(root)

        self.assertEqual(context["project"]["name"], "ssh-remote-control")
        self.assertEqual(context["project"]["version"], "0.1.0")
        self.assertEqual(context["git"]["commit"], "abc123")
        self.assertTrue(context["git"]["dirty"])
        self.assertIn("generated_at_utc", context)
        self.assertIn("python_version", context["builder"])
        self.assertEqual(context["plugin"]["version"], "0.1.0")

    def test_evaluate_release_validation_detects_mismatches(self) -> None:
        validation = remote_cli._evaluate_release_validation(
            {
                "project": {"version": "0.1.0"},
                "plugin": {"version": "0.2.0"},
                "git": {"dirty": True},
            },
            expected_version="0.3.0",
        )

        self.assertFalse(validation["plugin_version_matches_project"])
        self.assertFalse(validation["git_clean"])
        self.assertFalse(validation["expected_version_matches_project"])
        self.assertIn("plugin-package-version-mismatch", validation["warnings"])
        self.assertIn("git-dirty", validation["warnings"])
        self.assertIn("expected-version-mismatch", validation["warnings"])

    def test_package_strict_release_returns_nonzero_on_validation_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='ssh-remote-control'\nversion='0.1.0'\n")
            scripts_dir = root / "scripts"
            scripts_dir.mkdir()
            (scripts_dir / "build_plugin_bundle.py").write_text("print('ok')\n")

            with patch.object(
                remote_cli,
                "_collect_release_context",
                return_value={
                    "generated_at_utc": "2026-04-11T00:00:00Z",
                    "project": {"name": "ssh-remote-control", "version": "0.1.0"},
                    "plugin": {"name": "ssh-remote-control", "version": "0.2.0"},
                    "git": {"commit": "abc123", "dirty": False},
                    "builder": {"python_version": "3.11.9", "python_executable": "/tmp/python", "platform": "darwin"},
                },
            ):
                with patch.object(remote_cli, "_project_root", return_value=root):
                    with patch("sys.stdout", new_callable=StringIO) as stdout:
                        exit_code = remote_cli.main(["package", "--json", "--skip-tests", "--skip-build", "--skip-bundle", "--strict-release"])

        self.assertEqual(exit_code, 1)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "failed")
        self.assertIn("plugin-package-version-mismatch", payload["validation"]["warnings"])

    def test_profile_doctor_json_reports_load_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_path = Path(tmp) / "profiles.json"
            store_path.write_text(
                json.dumps(
                    {
                        "default_profile": "broken",
                        "profiles": {
                            "broken": {"name": "broken", "host": "example.com", "remote_root": "relative/path"},
                            "prod": {"name": "prod", "host": "example.com", "remote_root": "/srv/app"},
                        },
                    }
                )
            )
            store = ProfileStore(store_path)

            with patch.object(remote_cli, "_profile_store", return_value=store):
                with patch("sys.stdout", new_callable=StringIO) as stdout:
                    exit_code = remote_cli.main(["profile", "doctor", "--json"])

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "warning")
        self.assertEqual(payload["profile_count"], 1)
        self.assertEqual(payload["default_profile"], "prod")
        self.assertEqual(payload["invalid_profile_count"], 1)
        self.assertEqual(payload["warnings"][0]["name"], "broken")

    def test_profile_doctor_rewrite_removes_invalid_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_path = Path(tmp) / "profiles.json"
            store_path.write_text(
                json.dumps(
                    {
                        "default_profile": "prod",
                        "profiles": {
                            "prod": {"name": "prod", "host": "example.com", "remote_root": "/srv/app"},
                            "broken": {"name": "broken", "host": "example.com", "remote_root": "relative/path"},
                        },
                    }
                )
            )
            store = ProfileStore(store_path)

            with patch.object(remote_cli, "_profile_store", return_value=store):
                with patch("sys.stdout", new_callable=StringIO) as stdout:
                    exit_code = remote_cli.main(["profile", "doctor", "--json", "--rewrite"])

            rewritten_payload = json.loads(store_path.read_text())

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "rewritten")
        self.assertEqual(payload["invalid_profile_count"], 0)
        self.assertEqual(payload["warnings"], [])
        self.assertEqual(sorted(rewritten_payload["profiles"].keys()), ["prod"])

    def test_support_bundle_writes_archive_with_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle_path = Path(tmp) / "support.tar.gz"
            profile = SSHProfile(name="prod", host="example.com", remote_root="/srv/app")
            store = MagicMock()
            store.path = Path(tmp) / "profiles.json"
            store.get_default_profile_name.return_value = "prod"
            store.list_profiles.return_value = [profile]
            store.get_load_warnings.return_value = []
            manager = MagicMock()
            manager.status.return_value = {"status": "ok", "pid": 123, "workspace": "/srv/app"}
            manager.doctor.return_value = {"status": "ok", "remote_auth_status": "logged-in", "authorization": "Bearer abc.def"}
            manager.read_remote_logs.return_value = "OPENAI_API_KEY=sk-secret\nhello\n"

            with patch.object(remote_cli, "_require_profile", return_value=profile):
                with patch.object(remote_cli, "_profile_store", return_value=store):
                    with patch("ssh_remote_control.remote_cli.RemoteCodexManager", return_value=manager):
                        with patch.object(remote_cli, "_utc_now_iso", return_value="2026-04-11T01:02:03Z"):
                            with patch("sys.stdout", new_callable=StringIO) as stdout:
                                exit_code = remote_cli.main(
                                    ["support-bundle", "prod", "--json", "--output", str(bundle_path), "--lines", "25"]
                                )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["bundle"], str(bundle_path))
            self.assertEqual(len(payload["included_files"]), 6)

            with tarfile.open(bundle_path, "r:gz") as archive:
                names = sorted(archive.getnames())
                self.assertIn("codex-remote-support-prod-20260411-010203Z/manifest.json", names)
                self.assertIn("codex-remote-support-prod-20260411-010203Z/profile.json", names)
                self.assertIn("codex-remote-support-prod-20260411-010203Z/status.json", names)
                manifest = json.load(
                    archive.extractfile("codex-remote-support-prod-20260411-010203Z/manifest.json")  # type: ignore[arg-type]
                )
                self.assertEqual(manifest["status"], "ok")
                self.assertTrue(manifest["redacted"])
                self.assertEqual(manifest["steps"]["logs"], "ok")
                doctor = json.load(
                    archive.extractfile("codex-remote-support-prod-20260411-010203Z/doctor.json")  # type: ignore[arg-type]
                )
                self.assertEqual(doctor["payload"]["authorization"], "Bearer REDACTED")
                logs = archive.extractfile("codex-remote-support-prod-20260411-010203Z/logs.txt")  # type: ignore[arg-type]
                self.assertIsNotNone(logs)
                assert logs is not None
                self.assertEqual(logs.read().decode("utf-8"), "OPENAI_API_KEY=REDACTED\nhello\n")

    def test_support_bundle_can_disable_redaction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle_path = Path(tmp) / "support.tar.gz"
            profile = SSHProfile(name="prod", host="example.com", remote_root="/srv/app")
            store = MagicMock()
            store.path = Path(tmp) / "profiles.json"
            store.get_default_profile_name.return_value = "prod"
            store.list_profiles.return_value = [profile]
            store.get_load_warnings.return_value = []
            manager = MagicMock()
            manager.status.return_value = {"status": "ok"}
            manager.doctor.return_value = {"status": "ok", "token": "sk-secret"}
            manager.read_remote_logs.return_value = "authorization: Bearer abc.def\n"

            with patch.object(remote_cli, "_require_profile", return_value=profile):
                with patch.object(remote_cli, "_profile_store", return_value=store):
                    with patch("ssh_remote_control.remote_cli.RemoteCodexManager", return_value=manager):
                        with patch.object(remote_cli, "_utc_now_iso", return_value="2026-04-11T01:02:03Z"):
                            with patch("sys.stdout", new_callable=StringIO) as stdout:
                                exit_code = remote_cli.main(
                                    ["support-bundle", "prod", "--json", "--output", str(bundle_path), "--no-redact"]
                                )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["status"], "ok")

            with tarfile.open(bundle_path, "r:gz") as archive:
                manifest = json.load(
                    archive.extractfile("codex-remote-support-prod-20260411-010203Z/manifest.json")  # type: ignore[arg-type]
                )
                self.assertFalse(manifest["redacted"])
                doctor = json.load(
                    archive.extractfile("codex-remote-support-prod-20260411-010203Z/doctor.json")  # type: ignore[arg-type]
                )
                self.assertEqual(doctor["payload"]["token"], "sk-secret")
                logs = archive.extractfile("codex-remote-support-prod-20260411-010203Z/logs.txt")  # type: ignore[arg-type]
                self.assertIsNotNone(logs)
                assert logs is not None
                self.assertEqual(logs.read().decode("utf-8"), "authorization: Bearer abc.def\n")

    def test_support_bundle_captures_remote_failures_in_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle_path = Path(tmp) / "support.tar.gz"
            profile = SSHProfile(name="prod", host="example.com", remote_root="/srv/app")
            store = MagicMock()
            store.path = Path(tmp) / "profiles.json"
            store.get_default_profile_name.return_value = "prod"
            store.list_profiles.return_value = [profile]
            store.get_load_warnings.return_value = [{"name": "broken", "error": "remote_root must be an absolute POSIX path"}]
            manager = MagicMock()
            manager.status.side_effect = RuntimeError("ssh down")
            manager.doctor.return_value = {"status": "ok"}
            manager.read_remote_logs.side_effect = RuntimeError("authorization: Bearer abc.def")

            with patch.object(remote_cli, "_require_profile", return_value=profile):
                with patch.object(remote_cli, "_profile_store", return_value=store):
                    with patch("ssh_remote_control.remote_cli.RemoteCodexManager", return_value=manager):
                        with patch.object(remote_cli, "_utc_now_iso", return_value="2026-04-11T01:02:03Z"):
                            with patch("sys.stdout", new_callable=StringIO) as stdout:
                                exit_code = remote_cli.main(
                                    ["support-bundle", "prod", "--json", "--output", str(bundle_path)]
                                )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["status"], "warning")

            with tarfile.open(bundle_path, "r:gz") as archive:
                manifest = json.load(
                    archive.extractfile("codex-remote-support-prod-20260411-010203Z/manifest.json")  # type: ignore[arg-type]
                )
                self.assertEqual(manifest["status"], "warning")
                self.assertEqual(manifest["steps"]["status"], "failed")
                self.assertEqual(manifest["steps"]["profile_store"], "warning")
                status_payload = json.load(
                    archive.extractfile("codex-remote-support-prod-20260411-010203Z/status.json")  # type: ignore[arg-type]
                )
                self.assertEqual(status_payload["status"], "failed")
                self.assertEqual(status_payload["error"], "ssh down")
                logs_error = archive.extractfile("codex-remote-support-prod-20260411-010203Z/logs.error.txt")  # type: ignore[arg-type]
                self.assertIsNotNone(logs_error)
                assert logs_error is not None
                self.assertEqual(logs_error.read().decode("utf-8"), "authorization=REDACTED\n")

    def test_hidden_complete_suggests_top_level_commands(self) -> None:
        with patch("sys.stdout", new_callable=StringIO) as stdout:
            exit_code = remote_cli.main(["_complete", "--current", "op"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout.getvalue().strip().splitlines(), ["open"])

    def test_hidden_complete_suggests_profiles_for_open(self) -> None:
        with patch.object(remote_cli, "_completion_profile_names", return_value=["prod", "staging"]):
            with patch("sys.stdout", new_callable=StringIO) as stdout:
                exit_code = remote_cli.main(["_complete", "--current", "pr", "--", "open"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout.getvalue().strip().splitlines(), ["prod"])

    def test_hidden_complete_suggests_profiles_and_aliases_for_connect(self) -> None:
        with patch.object(remote_cli, "_completion_profile_names", return_value=["prod"]):
            with patch.object(remote_cli, "_completion_ssh_aliases", return_value=["llm.ezbuilder.app", "prod"]):
                with patch("sys.stdout", new_callable=StringIO) as stdout:
                    exit_code = remote_cli.main(["_complete", "--current", "l", "--", "connect"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout.getvalue().strip().splitlines(), ["llm.ezbuilder.app"])

    @patch("ssh_remote_control.remote_cli.subprocess.run")
    def test_package_strict_release_fails_fast_before_packaging_steps(
        self,
        run_mock: MagicMock,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='ssh-remote-control'\nversion='0.1.0'\n")
            scripts_dir = root / "scripts"
            scripts_dir.mkdir()
            (scripts_dir / "build_plugin_bundle.py").write_text("print('ok')\n")

            with patch.object(
                remote_cli,
                "_collect_release_context",
                return_value={
                    "generated_at_utc": "2026-04-11T00:00:00Z",
                    "project": {"name": "ssh-remote-control", "version": "0.1.0"},
                    "plugin": {"name": "ssh-remote-control", "version": "0.1.0"},
                    "git": {"commit": "abc123", "dirty": True},
                    "builder": {"python_version": "3.11.9", "python_executable": "/tmp/python", "platform": "darwin"},
                },
            ):
                with patch.object(remote_cli, "_project_root", return_value=root):
                    with patch("sys.stdout", new_callable=StringIO) as stdout:
                        exit_code = remote_cli.main(["package", "--json", "--strict-release"])

        self.assertEqual(exit_code, 1)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "failed")
        self.assertIn("git-dirty", payload["validation"]["warnings"])
        self.assertNotIn("tests", payload)
        self.assertNotIn("build", payload)
        self.assertNotIn("bundle", payload)
        run_mock.assert_not_called()

    def test_package_fails_when_no_release_artifacts_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='ssh-remote-control'\nversion='0.1.0'\n")
            scripts_dir = root / "scripts"
            scripts_dir.mkdir()
            (scripts_dir / "build_plugin_bundle.py").write_text("print('ok')\n")

            with patch.object(
                remote_cli,
                "_collect_release_context",
                return_value={
                    "generated_at_utc": "2026-04-11T00:00:00Z",
                    "project": {"name": "ssh-remote-control", "version": "0.1.0"},
                    "plugin": {"name": "ssh-remote-control", "version": "0.1.0"},
                    "git": {"commit": "abc123", "dirty": False},
                    "builder": {"python_version": "3.11.9", "python_executable": "/tmp/python", "platform": "darwin"},
                },
            ):
                with patch.object(remote_cli, "_project_root", return_value=root):
                    with patch("sys.stdout", new_callable=StringIO) as stdout:
                        exit_code = remote_cli.main(["package", "--json", "--skip-tests", "--skip-build", "--skip-bundle"])

        self.assertEqual(exit_code, 1)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "failed")
        self.assertIn("release-artifacts-missing", payload["validation"]["warnings"])

    def test_clean_release_outputs_removes_only_managed_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dist_dir = Path(tmp)
            managed = [
                dist_dir / "ssh_remote_control-0.1.0-py3-none-any.whl",
                dist_dir / "ssh_remote_control-0.1.0.tar.gz",
                dist_dir / "ssh-remote-control-plugin.zip",
                dist_dir / "SHA256SUMS",
                dist_dir / "release-manifest.json",
            ]
            for path in managed:
                path.write_text("x")
            keep = dist_dir / "notes.txt"
            keep.write_text("keep")

            removed = remote_cli._clean_release_outputs(dist_dir)

            self.assertEqual(
                sorted(Path(path).name for path in removed),
                sorted(path.name for path in managed),
            )
            self.assertTrue(keep.exists())
            for path in managed:
                self.assertFalse(path.exists(), path)

    def test_install_cli_can_write_shell_completion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_dir = root / "source-bin"
            source_dir.mkdir()
            source_path = source_dir / "codex-remote"
            source_path.write_text("#!/bin/sh\n")
            bin_dir = root / "bin"
            completion_path = root / "completions" / "_codex-remote"

            with patch("ssh_remote_control.remote_cli.shutil.which", return_value=str(source_path)):
                with patch.object(remote_cli, "_completion_install_target", return_value=completion_path):
                    with patch.object(remote_cli, "_render_completion_script", return_value="completion-script\n"):
                        with patch("sys.stdout", new_callable=StringIO) as stdout:
                            exit_code = remote_cli.main(
                                [
                                    "install-cli",
                                    "--bin-dir",
                                    str(bin_dir),
                                    "--shell-completion",
                                    "zsh",
                                    "--json",
                                ]
                            )

            launcher_exists = (bin_dir / "codex-remote").is_symlink()
            completion_contents = completion_path.read_text()

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "installed")
        self.assertEqual(payload["completion_shell"], "zsh")
        self.assertTrue(launcher_exists)
        self.assertEqual(completion_contents, "completion-script\n")

    def test_open_parser_allows_omitting_profile_name(self) -> None:
        parser = remote_cli._build_parser()

        args, extra = parser.parse_known_args(["open", "--local-port", "4908"])

        self.assertEqual(args.command, "open")
        self.assertIsNone(args.profile_name)
        self.assertEqual(args.local_port, 4908)
        self.assertEqual(extra, [])

    def test_init_parser_accepts_profile_and_setup_options(self) -> None:
        parser = remote_cli._build_parser()

        args, extra = parser.parse_known_args(
            [
                "init",
                "prod",
                "--ssh-config-host",
                "llm.ezbuilder.app",
                "--remote-root",
                "/tmp",
                "--codex-version",
                "0.118.0",
                "--service-scope",
                "user",
                "--default-model",
                "gpt-5.4",
                "--default-cd",
                "/tmp/app",
                "--sync-local-on-up",
                "--api-key-env",
                "OPENAI_API_KEY",
                "--auto-login-on-up",
                "--local-port",
                "4905",
                "--sync-local",
            ]
        )

        self.assertEqual(args.command, "init")
        self.assertEqual(args.name, "prod")
        self.assertEqual(args.ssh_config_host, "llm.ezbuilder.app")
        self.assertEqual(args.remote_root, "/tmp")
        self.assertEqual(args.codex_version, "0.118.0")
        self.assertEqual(args.service_scope, "user")
        self.assertEqual(args.default_model, "gpt-5.4")
        self.assertEqual(args.default_cd, "/tmp/app")
        self.assertTrue(args.sync_local_on_up)
        self.assertEqual(args.api_key_env, "OPENAI_API_KEY")
        self.assertTrue(args.auto_login_on_up)
        self.assertEqual(args.local_port, 4905)
        self.assertTrue(args.sync_local)
        self.assertEqual(extra, [])

    def test_init_parser_accepts_launch_and_forwarded_codex_args(self) -> None:
        parser = remote_cli._build_parser()

        args, extra = parser.parse_known_args(
            [
                "init",
                "prod",
                "--ssh-config-host",
                "llm.ezbuilder.app",
                "--remote-root",
                "/tmp",
                "--launch",
                "--",
                "-m",
                "gpt-5.4",
            ]
        )

        self.assertEqual(args.command, "init")
        self.assertTrue(args.launch)
        self.assertEqual(extra, ["--", "-m", "gpt-5.4"])

    def test_connect_parser_accepts_setup_and_forwarded_codex_args(self) -> None:
        parser = remote_cli._build_parser()

        args, extra = parser.parse_known_args(
            [
                "connect",
                "llm.ezbuilder.app",
                "--name",
                "prod",
                "--remote-root",
                "/tmp",
                "--json",
                "--strict",
                "--",
                "continue",
            ]
        )

        self.assertEqual(args.command, "connect")
        self.assertEqual(args.target, "llm.ezbuilder.app")
        self.assertEqual(args.name, "prod")
        self.assertEqual(args.remote_root, "/tmp")
        self.assertTrue(args.json)
        self.assertTrue(args.strict)
        self.assertEqual(extra, ["--", "continue"])

    def test_up_launches_after_preflight_when_requested(self) -> None:
        profile = SSHProfile(
            name="prod",
            host="llm.ezbuilder.app",
            ssh_config_host="llm.ezbuilder.app",
            remote_root="/tmp",
        )
        manager = MagicMock()
        manager.up.return_value = {
            "status": "ok",
            "install_action": "",
            "local_install_action": "",
            "runtime": "service",
            "local_codex_version": "codex-cli 0.118.0",
            "remote_codex_version": "codex-cli 0.118.0",
            "local_port": 4906,
            "remote_port": 4500,
            "workspace": "/tmp",
            "warning": "",
        }
        manager.launch.return_value = 0

        with patch.object(remote_cli, "_require_profile", return_value=profile):
            with patch.object(remote_cli, "RemoteCodexManager", return_value=manager):
                exit_code = remote_cli.main(
                    ["up", "prod", "--launch", "--local-port", "4906", "--", "-m", "gpt-5.4"]
                )

        self.assertEqual(exit_code, 0)
        manager.up.assert_called_once_with(profile, local_port=4906, sync_local=False)
        manager.launch.assert_called_once_with(
            profile,
            extra_args=["-m", "gpt-5.4"],
            local_port=4906,
        )

    def test_init_saves_profile_and_launches_when_requested(self) -> None:
        manager = MagicMock()
        manager.up.return_value = {
            "status": "ok",
            "install_action": "",
            "local_install_action": "",
            "auth_action": "",
            "runtime": "service",
            "local_codex_version": "codex-cli 0.118.0",
            "remote_codex_version": "codex-cli 0.118.0",
            "local_port": 4907,
            "remote_port": 4500,
            "workspace": "/tmp",
            "warning": "",
        }
        manager.launch.return_value = 0
        store = MagicMock()

        with patch.object(remote_cli, "_profile_store", return_value=store):
            with patch.object(remote_cli, "RemoteCodexManager", return_value=manager):
                exit_code = remote_cli.main(
                    [
                        "init",
                        "prod",
                        "--ssh-config-host",
                        "llm.ezbuilder.app",
                        "--remote-root",
                        "/tmp",
                        "--launch",
                        "--local-port",
                        "4907",
                        "--",
                        "-m",
                        "gpt-5.4",
                    ]
                )

        self.assertEqual(exit_code, 0)
        saved_profile = store.save_profile.call_args.args[0]
        self.assertEqual(saved_profile.name, "prod")
        self.assertEqual(saved_profile.ssh_config_host, "llm.ezbuilder.app")
        manager.up.assert_called_once_with(saved_profile, local_port=4907, sync_local=False)
        manager.launch.assert_called_once_with(
            saved_profile,
            extra_args=["-m", "gpt-5.4"],
            local_port=4907,
        )

    def test_connect_reuses_existing_profile_and_sets_default(self) -> None:
        profile = SSHProfile(
            name="prod",
            host="llm.ezbuilder.app",
            ssh_config_host="llm.ezbuilder.app",
            remote_root="/tmp",
            default_model="gpt-5.4",
        )
        manager = MagicMock()
        manager.up.return_value = {
            "status": "ok",
            "install_action": "",
            "local_install_action": "",
            "auth_action": "",
            "runtime": "service",
            "local_codex_version": "codex-cli 0.118.0",
            "remote_codex_version": "codex-cli 0.118.0",
            "remote_auth_status": "logged-in",
            "local_port": 4913,
            "remote_port": 4500,
            "workspace": "/tmp",
            "warning": "",
        }
        manager.launch.return_value = 0
        store = MagicMock()
        store.get_profile.return_value = profile

        with patch.object(remote_cli, "_profile_store", return_value=store):
            with patch.object(remote_cli, "RemoteCodexManager", return_value=manager):
                exit_code = remote_cli.main(["connect", "prod", "--local-port", "4913"])

        self.assertEqual(exit_code, 0)
        store.set_default_profile.assert_called_once_with("prod")
        manager.up.assert_called_once_with(profile, local_port=4913, sync_local=False)
        manager.launch.assert_called_once_with(
            profile,
            extra_args=["-m", "gpt-5.4"],
            local_port=4913,
        )

    def test_connect_imports_alias_when_profile_missing(self) -> None:
        manager = MagicMock()
        manager.up.return_value = {
            "status": "ok",
            "install_action": "",
            "local_install_action": "",
            "auth_action": "",
            "runtime": "service",
            "local_codex_version": "codex-cli 0.118.0",
            "remote_codex_version": "codex-cli 0.118.0",
            "remote_auth_status": "logged-in",
            "local_port": 4914,
            "remote_port": 4500,
            "workspace": "/tmp",
            "warning": "",
        }
        store = MagicMock()
        store.get_profile.return_value = None

        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config"
            config_path.write_text(
                "Host llm.ezbuilder.app\n"
                "  HostName 72.62.250.251\n"
                "  User root\n"
            )
            with patch.object(remote_cli, "_profile_store", return_value=store):
                with patch.object(remote_cli, "RemoteCodexManager", return_value=manager):
                    exit_code = remote_cli.main(
                        [
                            "connect",
                            "llm.ezbuilder.app",
                            "--name",
                            "prod",
                            "--ssh-config-path",
                            str(config_path),
                            "--remote-root",
                            "/tmp",
                            "--no-launch",
                            "--json",
                        ]
                    )

        self.assertEqual(exit_code, 0)
        saved_profile = store.save_profile.call_args.args[0]
        self.assertEqual(saved_profile.name, "prod")
        self.assertEqual(saved_profile.ssh_config_host, "llm.ezbuilder.app")
        store.set_default_profile.assert_called_once_with("prod")
        manager.up.assert_called_once()

    def test_connect_strict_returns_nonzero_when_warning_present(self) -> None:
        profile = SSHProfile(name="prod", host="example.com")
        manager = MagicMock()
        manager.up.return_value = {
            "status": "ok",
            "install_action": "",
            "local_install_action": "",
            "auth_action": "",
            "runtime": "service",
            "local_codex_version": "codex-cli 0.116.0",
            "remote_codex_version": "codex-cli 0.118.0",
            "remote_auth_status": "logged-in",
            "local_port": 4915,
            "remote_port": 4500,
            "workspace": "/tmp",
            "warning": "local-remote-version-mismatch",
        }
        store = MagicMock()
        store.get_profile.return_value = profile

        with patch.object(remote_cli, "_profile_store", return_value=store):
            with patch.object(remote_cli, "RemoteCodexManager", return_value=manager):
                exit_code = remote_cli.main(["connect", "prod", "--no-launch", "--strict"])

        self.assertEqual(exit_code, 1)

    def test_init_launch_applies_saved_defaults(self) -> None:
        manager = MagicMock()
        manager.up.return_value = {
            "status": "ok",
            "install_action": "",
            "local_install_action": "",
            "auth_action": "",
            "runtime": "service",
            "local_codex_version": "codex-cli 0.118.0",
            "remote_codex_version": "codex-cli 0.118.0",
            "local_port": 4910,
            "remote_port": 4500,
            "workspace": "/tmp",
            "warning": "",
        }
        manager.launch.return_value = 0
        store = MagicMock()

        with patch.object(remote_cli, "_profile_store", return_value=store):
            with patch.object(remote_cli, "RemoteCodexManager", return_value=manager):
                exit_code = remote_cli.main(
                    [
                        "init",
                        "prod",
                        "--ssh-config-host",
                        "llm.ezbuilder.app",
                        "--remote-root",
                        "/tmp",
                        "--default-model",
                        "gpt-5.4",
                        "--default-cd",
                        "/tmp/app",
                        "--launch",
                        "--local-port",
                        "4910",
                    ]
                )

        self.assertEqual(exit_code, 0)
        saved_profile = store.save_profile.call_args.args[0]
        manager.launch.assert_called_once_with(
            saved_profile,
            extra_args=["-m", "gpt-5.4", "-C", "/tmp/app"],
            local_port=4910,
        )

    def test_open_uses_profile_defaults_and_allows_overrides(self) -> None:
        profile = SSHProfile(
            name="prod",
            host="llm.ezbuilder.app",
            ssh_config_host="llm.ezbuilder.app",
            remote_root="/tmp",
            default_model="gpt-5.4",
            default_cd="/tmp/app",
            sync_local_on_up=True,
            api_key_env="OPENAI_API_KEY",
            auto_login_on_up=True,
        )
        manager = MagicMock()
        manager.up.return_value = {
            "status": "ok",
            "install_action": "",
            "local_install_action": "",
            "runtime": "service",
            "local_codex_version": "codex-cli 0.118.0",
            "remote_codex_version": "codex-cli 0.118.0",
            "local_port": 4908,
            "remote_port": 4500,
            "workspace": "/tmp",
            "warning": "",
        }
        manager.launch.return_value = 0

        with patch.object(remote_cli, "_require_profile", return_value=profile):
            with patch.object(remote_cli, "RemoteCodexManager", return_value=manager):
                exit_code = remote_cli.main(["open", "prod", "--local-port", "4908"])

        self.assertEqual(exit_code, 0)
        manager.up.assert_called_once_with(
            profile,
            local_port=4908,
            sync_local=True,
            auto_login=True,
            api_key_env="OPENAI_API_KEY",
        )
        manager.launch.assert_called_once_with(
            profile,
            extra_args=["-m", "gpt-5.4", "-C", "/tmp/app"],
            local_port=4908,
        )

    def test_up_launch_applies_profile_defaults(self) -> None:
        profile = SSHProfile(
            name="prod",
            host="llm.ezbuilder.app",
            ssh_config_host="llm.ezbuilder.app",
            remote_root="/tmp",
            default_model="gpt-5.4",
            default_cd="/tmp/app",
        )
        manager = MagicMock()
        manager.up.return_value = {
            "status": "ok",
            "install_action": "",
            "local_install_action": "",
            "auth_action": "",
            "runtime": "service",
            "local_codex_version": "codex-cli 0.118.0",
            "remote_codex_version": "codex-cli 0.118.0",
            "local_port": 4911,
            "remote_port": 4500,
            "workspace": "/tmp",
            "warning": "",
        }
        manager.launch.return_value = 0

        with patch.object(remote_cli, "_require_profile", return_value=profile):
            with patch.object(remote_cli, "RemoteCodexManager", return_value=manager):
                exit_code = remote_cli.main(["up", "prod", "--launch", "--local-port", "4911"])

        self.assertEqual(exit_code, 0)
        manager.launch.assert_called_once_with(
            profile,
            extra_args=["-m", "gpt-5.4", "-C", "/tmp/app"],
            local_port=4911,
        )

    def test_open_skips_profile_defaults_when_user_overrides(self) -> None:
        profile = SSHProfile(
            name="prod",
            host="llm.ezbuilder.app",
            ssh_config_host="llm.ezbuilder.app",
            remote_root="/tmp",
            default_model="gpt-5.4",
            default_cd="/tmp/app",
            sync_local_on_up=True,
            api_key_env="OPENAI_API_KEY",
            auto_login_on_up=True,
        )
        manager = MagicMock()
        manager.up.return_value = {
            "status": "ok",
            "install_action": "",
            "local_install_action": "",
            "runtime": "service",
            "local_codex_version": "codex-cli 0.118.0",
            "remote_codex_version": "codex-cli 0.118.0",
            "local_port": 4909,
            "remote_port": 4500,
            "workspace": "/tmp",
            "warning": "",
        }
        manager.launch.return_value = 0

        with patch.object(remote_cli, "_require_profile", return_value=profile):
            with patch.object(remote_cli, "RemoteCodexManager", return_value=manager):
                exit_code = remote_cli.main(
                    ["open", "prod", "--local-port", "4909", "--", "-m", "gpt-5.5", "-C", "/override"]
                )

        self.assertEqual(exit_code, 0)
        manager.up.assert_called_once_with(
            profile,
            local_port=4909,
            sync_local=True,
            auto_login=True,
            api_key_env="OPENAI_API_KEY",
        )
        manager.launch.assert_called_once_with(
            profile,
            extra_args=["-m", "gpt-5.5", "-C", "/override"],
            local_port=4909,
        )

    def test_auth_login_uses_profile_api_key_env_by_default(self) -> None:
        profile = SSHProfile(
            name="prod",
            host="llm.ezbuilder.app",
            ssh_config_host="llm.ezbuilder.app",
            remote_root="/tmp",
            api_key_env="OPENAI_API_KEY",
        )
        manager = MagicMock()
        manager.login_remote_codex.return_value = {
            "status": "ok",
            "auth_status": "logged-in",
            "auth_message": "Logged in using API key",
        }

        with patch.object(remote_cli, "_require_profile", return_value=profile):
            with patch.object(remote_cli, "RemoteCodexManager", return_value=manager):
                exit_code = remote_cli.main(["auth-login", "prod"])

        self.assertEqual(exit_code, 0)
        manager.login_remote_codex.assert_called_once_with(profile, api_key_env="OPENAI_API_KEY")

    def test_profile_show_json_redacts_key_path_value(self) -> None:
        profile = SSHProfile(
            name="prod",
            host="llm.ezbuilder.app",
            ssh_config_host="llm.ezbuilder.app",
            remote_root="/tmp",
            key_path="~/.ssh/id_ed25519",
            service_scope="user",
        )

        with patch.object(remote_cli, "_require_profile", return_value=profile):
            with patch("sys.stdout", new_callable=StringIO) as stdout:
                exit_code = remote_cli.main(["profile", "show", "prod", "--json"])

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn('"service_scope": "user"', output)
        self.assertIn('"key_path_configured": true', output)
        self.assertNotIn("id_ed25519", output)

    def test_profile_delete_uses_store(self) -> None:
        store = MagicMock()
        store.delete_profile.return_value = True

        with patch.object(remote_cli, "_profile_store", return_value=store):
            exit_code = remote_cli.main(["profile", "delete", "prod"])

        self.assertEqual(exit_code, 0)
        store.delete_profile.assert_called_once_with("prod")

    def test_profile_use_sets_default_profile(self) -> None:
        store = MagicMock()
        store.set_default_profile.return_value = SSHProfile(name="prod", host="example.com")

        with patch.object(remote_cli, "_profile_store", return_value=store):
            exit_code = remote_cli.main(["profile", "use", "prod"])

        self.assertEqual(exit_code, 0)
        store.set_default_profile.assert_called_once_with("prod")

    def test_profile_current_shows_default_profile(self) -> None:
        store = MagicMock()
        store.get_default_profile.return_value = SSHProfile(name="prod", host="example.com")
        store.get_default_profile_name.return_value = "prod"

        with patch.object(remote_cli, "_profile_store", return_value=store):
            with patch("sys.stdout", new_callable=StringIO) as stdout:
                exit_code = remote_cli.main(["profile", "current", "--json"])

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn('"name": "prod"', output)
        self.assertIn('"is_default": true', output)

    def test_doctor_json_outputs_payload_and_strict_fails_on_warning(self) -> None:
        profile = SSHProfile(name="prod", host="example.com")
        manager = MagicMock()
        manager.doctor.return_value = {
            "status": "ok",
            "local_codex_version": "codex-cli 0.116.0",
            "remote_codex_path": "/usr/local/bin/codex",
            "remote_codex_version": "codex-cli 0.118.0",
            "remote_auth_status": "logged-in",
            "remote_auth_message": "Logged in using ChatGPT",
            "local_port": 4900,
            "remote_port": 4500,
            "workspace": "/tmp",
            "warning": "local-remote-version-mismatch",
        }

        with patch.object(remote_cli, "_require_profile", return_value=profile):
            with patch.object(remote_cli, "RemoteCodexManager", return_value=manager):
                with patch("sys.stdout", new_callable=StringIO) as stdout:
                    exit_code = remote_cli.main(["doctor", "--json", "--strict"])

        self.assertEqual(exit_code, 1)
        self.assertIn('"warning": "local-remote-version-mismatch"', stdout.getvalue())

    def test_up_json_outputs_payload(self) -> None:
        profile = SSHProfile(name="prod", host="example.com")
        manager = MagicMock()
        manager.up.return_value = {
            "status": "ok",
            "install_action": "",
            "local_install_action": "",
            "auth_action": "",
            "runtime": "service",
            "local_codex_version": "codex-cli 0.118.0",
            "remote_codex_version": "codex-cli 0.118.0",
            "remote_auth_status": "logged-in",
            "local_port": 4900,
            "remote_port": 4500,
            "workspace": "/tmp",
            "warning": "",
        }

        with patch.object(remote_cli, "_require_profile", return_value=profile):
            with patch.object(remote_cli, "RemoteCodexManager", return_value=manager):
                with patch("sys.stdout", new_callable=StringIO) as stdout:
                    exit_code = remote_cli.main(["up", "--json"])

        self.assertEqual(exit_code, 0)
        self.assertIn('"runtime": "service"', stdout.getvalue())

    def test_status_json_outputs_payload(self) -> None:
        profile = SSHProfile(name="prod", host="example.com")
        manager = MagicMock()
        manager.status.return_value = {
            "status": "running",
            "pid": 31337,
            "port": 4500,
            "log_file": "/tmp/app-server.log",
            "workspace": "/tmp",
        }

        with patch.object(remote_cli, "_require_profile", return_value=profile):
            with patch.object(remote_cli, "RemoteCodexManager", return_value=manager):
                with patch("sys.stdout", new_callable=StringIO) as stdout:
                    exit_code = remote_cli.main(["status", "--json"])

        self.assertEqual(exit_code, 0)
        self.assertIn('"pid": 31337', stdout.getvalue())

    def test_logs_json_wraps_output(self) -> None:
        profile = SSHProfile(name="prod", host="example.com")
        manager = MagicMock()
        manager.read_remote_logs.return_value = "line1\nline2\n"

        with patch.object(remote_cli, "_require_profile", return_value=profile):
            with patch.object(remote_cli, "RemoteCodexManager", return_value=manager):
                with patch("sys.stdout", new_callable=StringIO) as stdout:
                    exit_code = remote_cli.main(["logs", "--json"])

        self.assertEqual(exit_code, 0)
        self.assertIn('"output": "line1\\nline2\\n"', stdout.getvalue())

    def test_profile_aliases_lists_non_wildcard_hosts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config"
            config_path.write_text(
                "Host llm.ezbuilder.app *.internal\n"
                "  HostName 72.62.250.251\n"
                "Host ezbuilder.app\n"
                "  HostName 72.62.76.20\n"
            )

            with patch("sys.stdout", new_callable=StringIO) as stdout:
                exit_code = remote_cli.main(
                    ["profile", "aliases", "--ssh-config-path", str(config_path)]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            stdout.getvalue().strip().splitlines(),
            [
                "llm.ezbuilder.app\thost=72.62.250.251\tuser=-\tport=22\tidentity_file=false",
                "ezbuilder.app\thost=72.62.76.20\tuser=-\tport=22\tidentity_file=false",
            ],
        )

    def test_profile_aliases_json_includes_ip_aliases_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config"
            config_path.write_text(
                "Host 72.62.250.251 llm.ezbuilder.app\n"
                "  HostName 72.62.250.251\n"
                "  User root\n"
                "  IdentityFile ~/.ssh/github_deploy_key\n"
            )

            with patch("sys.stdout", new_callable=StringIO) as stdout:
                exit_code = remote_cli.main(
                    ["profile", "aliases", "--ssh-config-path", str(config_path), "--all", "--json"]
                )

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn('"alias": "72.62.250.251"', output)
        self.assertIn('"alias": "llm.ezbuilder.app"', output)
        self.assertIn('"identity_file_configured": true', output)

    def test_profile_import_ssh_config_saves_profile_from_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config"
            config_path.write_text(
                "Host llm.ezbuilder.app\n"
                "  HostName 72.62.250.251\n"
                "  User root\n"
                "  Port 2202\n"
            )
            store = MagicMock()

            with patch.object(remote_cli, "_profile_store", return_value=store):
                exit_code = remote_cli.main(
                    [
                        "profile",
                        "import-ssh-config",
                        "prod",
                        "--alias",
                        "llm.ezbuilder.app",
                        "--ssh-config-path",
                        str(config_path),
                        "--remote-root",
                        "/tmp",
                        "--default-model",
                        "gpt-5.4",
                    ]
                )

        self.assertEqual(exit_code, 0)
        saved_profile = store.save_profile.call_args.args[0]
        self.assertEqual(saved_profile.name, "prod")
        self.assertEqual(saved_profile.host, "72.62.250.251")
        self.assertEqual(saved_profile.username, "root")
        self.assertEqual(saved_profile.port, 2202)
        self.assertEqual(saved_profile.ssh_config_host, "llm.ezbuilder.app")
        self.assertEqual(saved_profile.default_model, "gpt-5.4")
        self.assertIsNone(saved_profile.key_path)

    def test_profile_import_ssh_config_defaults_alias_to_profile_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config"
            config_path.write_text(
                "Host prod\n"
                "  HostName 72.62.250.251\n"
                "  User root\n"
            )
            store = MagicMock()

            with patch.object(remote_cli, "_profile_store", return_value=store):
                exit_code = remote_cli.main(
                    [
                        "profile",
                        "import-ssh-config",
                        "prod",
                        "--ssh-config-path",
                        str(config_path),
                        "--remote-root",
                        "/tmp",
                    ]
                )

        self.assertEqual(exit_code, 0)
        saved_profile = store.save_profile.call_args.args[0]
        self.assertEqual(saved_profile.ssh_config_host, "prod")

    def test_open_uses_default_profile_when_name_is_omitted(self) -> None:
        profile = SSHProfile(
            name="prod",
            host="llm.ezbuilder.app",
            ssh_config_host="llm.ezbuilder.app",
            remote_root="/tmp",
            default_model="gpt-5.4",
        )
        manager = MagicMock()
        manager.up.return_value = {
            "status": "ok",
            "install_action": "",
            "local_install_action": "",
            "auth_action": "",
            "runtime": "service",
            "local_codex_version": "codex-cli 0.118.0",
            "remote_codex_version": "codex-cli 0.118.0",
            "local_port": 4912,
            "remote_port": 4500,
            "workspace": "/tmp",
            "warning": "",
        }
        manager.launch.return_value = 0
        store = MagicMock()
        store.get_default_profile.return_value = profile

        with patch.object(remote_cli, "_profile_store", return_value=store):
            with patch.object(remote_cli, "RemoteCodexManager", return_value=manager):
                exit_code = remote_cli.main(["open", "--local-port", "4912"])

        self.assertEqual(exit_code, 0)
        manager.up.assert_called_once()
        manager.launch.assert_called_once_with(
            profile,
            extra_args=["-m", "gpt-5.4"],
            local_port=4912,
        )

    def test_service_install_parser_accepts_profile_name(self) -> None:
        parser = remote_cli._build_parser()

        args, extra = parser.parse_known_args(["service-install", "prod"])

        self.assertEqual(args.command, "service-install")
        self.assertEqual(args.profile_name, "prod")
        self.assertEqual(extra, [])

    def test_service_status_parser_accepts_profile_name(self) -> None:
        parser = remote_cli._build_parser()

        args, extra = parser.parse_known_args(["service-status", "prod"])

        self.assertEqual(args.command, "service-status")
        self.assertEqual(args.profile_name, "prod")
        self.assertEqual(extra, [])

    def test_install_codex_parser_accepts_optional_version(self) -> None:
        parser = remote_cli._build_parser()

        args, extra = parser.parse_known_args(
            ["install-codex", "prod", "--version", "0.118.0"]
        )

        self.assertEqual(args.command, "install-codex")
        self.assertEqual(args.profile_name, "prod")
        self.assertEqual(args.version, "0.118.0")
        self.assertEqual(extra, [])

    def test_upgrade_codex_parser_accepts_profile_name(self) -> None:
        parser = remote_cli._build_parser()

        args, extra = parser.parse_known_args(["upgrade-codex", "prod"])

        self.assertEqual(args.command, "upgrade-codex")
        self.assertEqual(args.profile_name, "prod")
        self.assertEqual(extra, [])


if __name__ == "__main__":
    unittest.main()
