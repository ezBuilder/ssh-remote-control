import subprocess
import unittest
from unittest.mock import patch

from ssh_remote_control.models import SSHProfile
from ssh_remote_control.remote_codex import RemoteCodexManager


class FakeProcess:
    def __init__(self, args: list[str]) -> None:
        self.args = args
        self.returncode = None
        self.pid = 4242
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        return ("", "")

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


class FakeRunner:
    def __init__(self) -> None:
        self.run_calls: list[tuple[list[str], dict[str, object]]] = []
        self.popen_calls: list[tuple[list[str], dict[str, object]]] = []
        self.run_results: list[subprocess.CompletedProcess[str]] = []
        self.processes: list[FakeProcess] = []

    def run(self, args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.run_calls.append((list(args), dict(kwargs)))
        if self.run_results:
            return self.run_results.pop(0)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    def popen(self, args: list[str], **kwargs: object) -> FakeProcess:
        self.popen_calls.append((list(args), dict(kwargs)))
        process = FakeProcess(list(args))
        self.processes.append(process)
        return process


class RemoteCodexManagerTests(unittest.TestCase):
    def _profile(self, **overrides: object) -> SSHProfile:
        base = SSHProfile(
            name="prod",
            host="ignored.example.com",
            ssh_config_host="llm.ezbuilder.app",
            remote_root="/srv/app",
        )
        payload = base.to_dict()
        payload.update(overrides)
        return SSHProfile.from_dict(payload)

    def test_ensure_remote_app_server_uses_ssh_alias_and_remote_codex_runtime(self) -> None:
        runner = FakeRunner()
        runner.run_results.extend(
            [
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "status=installed\n"
                        "helper_path=/root/.local/bin/codex-remote-app-server\n"
                        "helper_version=1\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "runtime=service\n"
                        "status=not-installed\n"
                        "port=4788\n"
                        "enabled=no\n"
                        "service_name=codex-app-server-prod.service\n"
                        "unit_path=/etc/systemd/system/codex-app-server-prod.service\n"
                        "log_file=/home/ubuntu/.codex/ssh-remote-control/codex/prod/app-server.log\n"
                        "workspace=/srv/app\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "status=started\n"
                        "pid=31337\n"
                        "port=4788\n"
                        "log_file=/home/ubuntu/.codex/ssh-remote-control/codex/prod/app-server.log\n"
                        "workspace=/srv/app\n"
                    ),
                    stderr="",
                ),
            ]
        )
        manager = RemoteCodexManager(runner=runner)

        result = manager.ensure_remote_app_server(
            self._profile(codex_binary="/opt/codex/bin/codex", codex_app_server_port=4788)
        )

        self.assertEqual(result["status"], "started")
        bootstrap_args = runner.run_calls[0][0]
        self.assertIn("codex-remote-app-server", bootstrap_args[-1])
        self.assertIn("service-status", runner.run_calls[1][0][-1])
        ssh_args = runner.run_calls[2][0]
        self.assertEqual(ssh_args[0], "ssh")
        self.assertIn("llm.ezbuilder.app", ssh_args)
        self.assertIn("exec sh -lc", ssh_args[-1])
        self.assertIn("codex-remote-app-server", ssh_args[-1])
        self.assertIn(" start ", ssh_args[-1])

    def test_launch_starts_tunnel_and_runs_local_codex_in_remote_mode(self) -> None:
        runner = FakeRunner()
        runner.run_results.extend(
            [
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "status=installed\n"
                        "helper_path=/root/.local/bin/codex-remote-app-server\n"
                        "helper_version=1\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "runtime=service\n"
                        "status=not-installed\n"
                        "port=4500\n"
                        "enabled=no\n"
                        "service_name=codex-app-server-prod.service\n"
                        "unit_path=/etc/systemd/system/codex-app-server-prod.service\n"
                        "log_file=/home/ubuntu/.codex/ssh-remote-control/codex/prod/app-server.log\n"
                        "workspace=/srv/app\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "status=already-running\n"
                        "pid=31337\n"
                        "port=4500\n"
                        "log_file=/home/ubuntu/.codex/ssh-remote-control/codex/prod/app-server.log\n"
                        "workspace=/srv/app\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            ]
        )
        manager = RemoteCodexManager(
            runner=runner,
            wait_for_tunnel_ready=lambda host, port, timeout: None,
        )

        exit_code = manager.launch(
            self._profile(),
            extra_args=["-m", "gpt-5.4"],
            local_port=4701,
        )

        self.assertEqual(exit_code, 0)
        tunnel_args = runner.popen_calls[0][0]
        self.assertEqual(tunnel_args[0], "ssh")
        self.assertIn("-L", tunnel_args)
        self.assertIn("127.0.0.1:4701:127.0.0.1:4500", tunnel_args)
        self.assertIn("llm.ezbuilder.app", tunnel_args)

        local_codex_args = runner.run_calls[3][0]
        self.assertEqual(
            local_codex_args,
            [
                "codex",
                "--remote",
                "ws://127.0.0.1:4701",
                "-C",
                "/srv/app",
                "-m",
                "gpt-5.4",
            ],
        )
        self.assertTrue(runner.processes[0].terminated)

    def test_launch_does_not_duplicate_remote_workspace_when_user_overrides_cd(self) -> None:
        runner = FakeRunner()
        runner.run_results.extend(
            [
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "status=installed\n"
                        "helper_path=/root/.local/bin/codex-remote-app-server\n"
                        "helper_version=1\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "runtime=service\n"
                        "status=not-installed\n"
                        "port=4500\n"
                        "enabled=no\n"
                        "service_name=codex-app-server-prod.service\n"
                        "unit_path=/etc/systemd/system/codex-app-server-prod.service\n"
                        "log_file=/home/ubuntu/.codex/ssh-remote-control/codex/prod/app-server.log\n"
                        "workspace=/srv/app\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "status=already-running\n"
                        "pid=31337\n"
                        "port=4500\n"
                        "log_file=/home/ubuntu/.codex/ssh-remote-control/codex/prod/app-server.log\n"
                        "workspace=/srv/app\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            ]
        )
        manager = RemoteCodexManager(
            runner=runner,
            wait_for_tunnel_ready=lambda host, port, timeout: None,
        )

        manager.launch(
            self._profile(),
            extra_args=["-C", "/srv/other", "continue the session"],
            local_port=4702,
        )

        local_codex_args = runner.run_calls[3][0]
        self.assertEqual(
            local_codex_args,
            [
                "codex",
                "--remote",
                "ws://127.0.0.1:4702",
                "-C",
                "/srv/other",
                "continue the session",
            ],
        )

    def test_probe_remote_codex_reports_binary_and_version(self) -> None:
        runner = FakeRunner()
        runner.run_results.append(
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=(
                    "status=ok\n"
                    "codex_path=/usr/local/bin/codex\n"
                    "codex_version=codex-cli 0.118.0\n"
                    "auth_status=logged-in\n"
                    "auth_message=Logged in using ChatGPT\n"
                ),
                stderr="",
            )
        )
        manager = RemoteCodexManager(runner=runner, auto_bootstrap=False)

        result = manager.probe_remote_codex(self._profile())

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["codex_path"], "/usr/local/bin/codex")
        self.assertEqual(result["codex_version"], "codex-cli 0.118.0")
        self.assertEqual(result["auth_status"], "logged-in")

    def test_doctor_reports_local_remote_and_tunnel_checks(self) -> None:
        runner = FakeRunner()
        runner.run_results.extend(
            [
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout="codex-cli 0.118.0\n",
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "status=ok\n"
                        "codex_path=/usr/local/bin/codex\n"
                        "codex_version=codex-cli 0.118.0\n"
                        "auth_status=logged-in\n"
                        "auth_message=Logged in using ChatGPT\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout="status=stopped\npid=\nport=4500\nlog_file=/root/.codex/app.log\nworkspace=/srv/app\n",
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout="status=started\npid=31337\nport=4500\nlog_file=/root/.codex/app.log\nworkspace=/srv/app\n",
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout="status=stopped\npid=31337\nport=4500\nlog_file=/root/.codex/app.log\nworkspace=/srv/app\n",
                    stderr="",
                ),
            ]
        )
        manager = RemoteCodexManager(
            runner=runner,
            wait_for_tunnel_ready=lambda host, port, timeout: None,
            auto_bootstrap=False,
        )

        result = manager.doctor(self._profile(), local_port=4900)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["local_codex_version"], "codex-cli 0.118.0")
        self.assertEqual(result["remote_codex_path"], "/usr/local/bin/codex")
        self.assertEqual(result["remote_codex_version"], "codex-cli 0.118.0")
        self.assertEqual(result["remote_auth_status"], "logged-in")
        self.assertEqual(result["remote_port"], 4500)
        self.assertEqual(result["local_port"], 4900)
        self.assertEqual(result["warning"], "")
        self.assertTrue(runner.processes[0].terminated)

    def test_doctor_flags_version_mismatch(self) -> None:
        runner = FakeRunner()
        runner.run_results.extend(
            [
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout="codex-cli 0.116.0\n",
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "status=ok\n"
                        "codex_path=/usr/local/bin/codex\n"
                        "codex_version=codex-cli 0.118.0\n"
                        "auth_status=logged-in\n"
                        "auth_message=Logged in using ChatGPT\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout="status=stopped\npid=\nport=4500\nlog_file=/root/.codex/app.log\nworkspace=/srv/app\n",
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout="status=started\npid=31337\nport=4500\nlog_file=/root/.codex/app.log\nworkspace=/srv/app\n",
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout="status=stopped\npid=31337\nport=4500\nlog_file=/root/.codex/app.log\nworkspace=/srv/app\n",
                    stderr="",
                ),
            ]
        )
        manager = RemoteCodexManager(
            runner=runner,
            wait_for_tunnel_ready=lambda host, port, timeout: None,
            auto_bootstrap=False,
        )

        result = manager.doctor(self._profile(), local_port=4900)

        self.assertEqual(result["warning"], "local-remote-version-mismatch")

    def test_doctor_flags_remote_auth_missing(self) -> None:
        runner = FakeRunner()
        runner.run_results.extend(
            [
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout="codex-cli 0.118.0\n",
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "status=ok\n"
                        "codex_path=/usr/local/bin/codex\n"
                        "codex_version=codex-cli 0.118.0\n"
                        "auth_status=logged-out\n"
                        "auth_message=Not logged in\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout="status=stopped\npid=\nport=4500\nlog_file=/root/.codex/app.log\nworkspace=/srv/app\n",
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout="status=started\npid=31337\nport=4500\nlog_file=/root/.codex/app.log\nworkspace=/srv/app\n",
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout="status=stopped\npid=31337\nport=4500\nlog_file=/root/.codex/app.log\nworkspace=/srv/app\n",
                    stderr="",
                ),
            ]
        )
        manager = RemoteCodexManager(
            runner=runner,
            wait_for_tunnel_ready=lambda host, port, timeout: None,
            auto_bootstrap=False,
        )

        result = manager.doctor(self._profile(), local_port=4900)

        self.assertEqual(result["remote_auth_status"], "logged-out")
        self.assertEqual(result["warning"], "remote-auth-missing")

    def test_login_remote_codex_uses_local_env_api_key(self) -> None:
        runner = FakeRunner()
        runner.run_results.append(
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=(
                    "status=ok\n"
                    "codex_path=/usr/local/bin/codex\n"
                    "codex_version=codex-cli 0.118.0\n"
                    "auth_status=logged-in\n"
                    "auth_message=Logged in using API key\n"
                ),
                stderr="",
            )
        )
        manager = RemoteCodexManager(runner=runner, auto_bootstrap=False)

        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}, clear=False):
            result = manager.login_remote_codex(self._profile(), api_key_env="OPENAI_API_KEY")

        self.assertEqual(result["auth_status"], "logged-in")
        kwargs = runner.run_calls[0][1]
        self.assertEqual(kwargs["input"], "sk-test")
        self.assertIn("login --with-api-key", runner.run_calls[0][0][-1])

    def test_read_remote_logs_returns_tail_output(self) -> None:
        runner = FakeRunner()
        runner.run_results.append(
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="line one\nline two\n",
                stderr="",
            )
        )
        manager = RemoteCodexManager(runner=runner, auto_bootstrap=False)

        output = manager.read_remote_logs(self._profile(), lines=25)

        self.assertEqual(output, "line one\nline two\n")
        ssh_args = runner.run_calls[0][0]
        self.assertIn("codex-remote-app-server", ssh_args[-1])
        self.assertIn(" logs ", ssh_args[-1])
        self.assertIn(" 25", ssh_args[-1])

    def test_bootstrap_installs_remote_helper(self) -> None:
        runner = FakeRunner()
        runner.run_results.append(
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=(
                    "status=installed\n"
                    "helper_path=/root/.local/bin/codex-remote-app-server\n"
                    "helper_version=1\n"
                ),
                stderr="",
            )
        )
        manager = RemoteCodexManager(runner=runner)

        result = manager.bootstrap(self._profile())

        self.assertEqual(result["status"], "installed")
        self.assertEqual(result["helper_path"], "/root/.local/bin/codex-remote-app-server")
        self.assertEqual(result["helper_version"], "1")
        ssh_args = runner.run_calls[0][0]
        self.assertIn("chmod 700", ssh_args[-1])
        self.assertIn("codex-remote-app-server", ssh_args[-1])

    def test_helper_command_uses_remote_home_expansion(self) -> None:
        manager = RemoteCodexManager(auto_bootstrap=False)

        command = manager._build_helper_command(self._profile(service_scope="user"), "status")

        self.assertIn('HELPER_PATH="$HOME/.local/bin/codex-remote-app-server"', command)
        self.assertNotIn("/root/${HOME}", command)
        self.assertIn(" user ", command)

    def test_service_install_and_status_use_helper_commands(self) -> None:
        runner = FakeRunner()
        runner.run_results.extend(
            [
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "status=installed\n"
                        "helper_path=/root/.local/bin/codex-remote-app-server\n"
                        "helper_version=1\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "status=installed\n"
                        "enabled=enabled\n"
                        "service_name=codex-app-server-prod.service\n"
                        "service_scope=user\n"
                        "unit_path=/etc/systemd/system/codex-app-server-prod.service\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "status=active\n"
                        "enabled=enabled\n"
                        "service_name=codex-app-server-prod.service\n"
                        "service_scope=user\n"
                        "unit_path=/etc/systemd/system/codex-app-server-prod.service\n"
                    ),
                    stderr="",
                ),
            ]
        )
        manager = RemoteCodexManager(runner=runner)

        installed = manager.service_install(self._profile(service_scope="user"))
        status = manager.service_status(self._profile(service_scope="user"))

        self.assertEqual(installed["status"], "installed")
        self.assertEqual(installed["enabled"], "enabled")
        self.assertEqual(installed["service_scope"], "user")
        self.assertEqual(status["status"], "active")
        self.assertEqual(status["service_name"], "codex-app-server-prod.service")
        self.assertEqual(status["service_scope"], "user")
        self.assertIn("install-service", runner.run_calls[1][0][-1])
        self.assertIn("service-status", runner.run_calls[2][0][-1])

    def test_ensure_remote_app_server_prefers_active_service(self) -> None:
        runner = FakeRunner()
        runner.run_results.extend(
            [
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "status=installed\n"
                        "helper_path=/root/.local/bin/codex-remote-app-server\n"
                        "helper_version=1\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "runtime=service\n"
                        "status=active\n"
                        "port=4500\n"
                        "enabled=enabled\n"
                        "service_name=codex-app-server-prod.service\n"
                        "unit_path=/etc/systemd/system/codex-app-server-prod.service\n"
                        "log_file=/root/.codex/app.log\n"
                        "workspace=/srv/app\n"
                    ),
                    stderr="",
                ),
            ]
        )
        manager = RemoteCodexManager(runner=runner)

        result = manager.ensure_remote_app_server(self._profile())

        self.assertEqual(result["runtime"], "service")
        self.assertEqual(result["status"], "active")
        self.assertEqual(len(runner.run_calls), 2)
        self.assertIn("service-status", runner.run_calls[1][0][-1])

    def test_stop_prefers_service_stop_when_service_is_installed(self) -> None:
        runner = FakeRunner()
        runner.run_results.extend(
            [
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "status=installed\n"
                        "helper_path=/root/.local/bin/codex-remote-app-server\n"
                        "helper_version=1\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "runtime=service\n"
                        "status=active\n"
                        "port=4500\n"
                        "enabled=enabled\n"
                        "service_name=codex-app-server-prod.service\n"
                        "unit_path=/etc/systemd/system/codex-app-server-prod.service\n"
                        "log_file=/root/.codex/app.log\n"
                        "workspace=/srv/app\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "runtime=service\n"
                        "status=inactive\n"
                        "port=4500\n"
                        "enabled=enabled\n"
                        "service_name=codex-app-server-prod.service\n"
                        "unit_path=/etc/systemd/system/codex-app-server-prod.service\n"
                        "log_file=/root/.codex/app.log\n"
                        "workspace=/srv/app\n"
                    ),
                    stderr="",
                ),
            ]
        )
        manager = RemoteCodexManager(runner=runner)

        result = manager.stop(self._profile())

        self.assertEqual(result["runtime"], "service")
        self.assertEqual(result["status"], "inactive")
        self.assertIn("service-stop", runner.run_calls[2][0][-1])

    def test_install_remote_codex_uses_npm_and_restarts_active_service(self) -> None:
        runner = FakeRunner()
        runner.run_results.extend(
            [
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "status=installed\n"
                        "helper_path=/root/.local/bin/codex-remote-app-server\n"
                        "helper_version=1\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "runtime=service\n"
                        "status=active\n"
                        "port=4500\n"
                        "enabled=enabled\n"
                        "service_name=codex-app-server-prod.service\n"
                        "unit_path=/etc/systemd/system/codex-app-server-prod.service\n"
                        "log_file=/root/.codex/app.log\n"
                        "workspace=/srv/app\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "status=installed\n"
                        "codex_path=/usr/local/bin/codex\n"
                        "codex_version=codex-cli 0.118.0\n"
                        "package_spec=@openai/codex@0.118.0\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "runtime=service\n"
                        "status=inactive\n"
                        "port=4500\n"
                        "enabled=enabled\n"
                        "service_name=codex-app-server-prod.service\n"
                        "unit_path=/etc/systemd/system/codex-app-server-prod.service\n"
                        "log_file=/root/.codex/app.log\n"
                        "workspace=/srv/app\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "runtime=service\n"
                        "status=active\n"
                        "port=4500\n"
                        "enabled=enabled\n"
                        "service_name=codex-app-server-prod.service\n"
                        "unit_path=/etc/systemd/system/codex-app-server-prod.service\n"
                        "log_file=/root/.codex/app.log\n"
                        "workspace=/srv/app\n"
                    ),
                    stderr="",
                ),
            ]
        )
        manager = RemoteCodexManager(runner=runner)

        result = manager.install_remote_codex(self._profile(), version="0.118.0")

        self.assertEqual(result["status"], "installed")
        self.assertEqual(result["codex_version"], "codex-cli 0.118.0")
        self.assertEqual(result["restart_status"], "active")
        install_args = runner.run_calls[2][0]
        self.assertIn("npm install -g @openai/codex@0.118.0", install_args[-1])
        self.assertIn("service-stop", runner.run_calls[3][0][-1])
        self.assertIn("service-start", runner.run_calls[4][0][-1])

    def test_upgrade_remote_codex_restarts_running_process(self) -> None:
        runner = FakeRunner()
        runner.run_results.extend(
            [
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "status=installed\n"
                        "helper_path=/root/.local/bin/codex-remote-app-server\n"
                        "helper_version=1\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "runtime=service\n"
                        "status=not-installed\n"
                        "port=4500\n"
                        "enabled=no\n"
                        "service_name=codex-app-server-prod.service\n"
                        "unit_path=/etc/systemd/system/codex-app-server-prod.service\n"
                        "log_file=/root/.codex/app.log\n"
                        "workspace=/srv/app\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "status=running\n"
                        "pid=31337\n"
                        "port=4500\n"
                        "log_file=/root/.codex/app.log\n"
                        "workspace=/srv/app\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "status=upgraded\n"
                        "codex_path=/usr/local/bin/codex\n"
                        "codex_version=codex-cli 0.119.0\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "runtime=service\n"
                        "status=not-installed\n"
                        "port=4500\n"
                        "enabled=no\n"
                        "service_name=codex-app-server-prod.service\n"
                        "unit_path=/etc/systemd/system/codex-app-server-prod.service\n"
                        "log_file=/root/.codex/app.log\n"
                        "workspace=/srv/app\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "status=stopped\n"
                        "pid=31337\n"
                        "port=4500\n"
                        "log_file=/root/.codex/app.log\n"
                        "workspace=/srv/app\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "runtime=service\n"
                        "status=not-installed\n"
                        "port=4500\n"
                        "enabled=no\n"
                        "service_name=codex-app-server-prod.service\n"
                        "unit_path=/etc/systemd/system/codex-app-server-prod.service\n"
                        "log_file=/root/.codex/app.log\n"
                        "workspace=/srv/app\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "status=started\n"
                        "pid=42424\n"
                        "port=4500\n"
                        "log_file=/root/.codex/app.log\n"
                        "workspace=/srv/app\n"
                    ),
                    stderr="",
                ),
            ]
        )
        manager = RemoteCodexManager(runner=runner)

        result = manager.upgrade_remote_codex(self._profile())

        self.assertEqual(result["status"], "upgraded")
        self.assertEqual(result["codex_version"], "codex-cli 0.119.0")
        self.assertEqual(result["restart_status"], "started")
        upgrade_args = runner.run_calls[3][0]
        self.assertIn(" --upgrade", upgrade_args[-1])
        self.assertIn(" stop ", runner.run_calls[5][0][-1])
        self.assertIn(" start ", runner.run_calls[7][0][-1])

    def test_up_installs_pinned_version_and_starts_service(self) -> None:
        runner = FakeRunner()
        runner.run_results.extend(
            [
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "status=installed\n"
                        "helper_path=/root/.local/bin/codex-remote-app-server\n"
                        "helper_version=1\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "runtime=service\n"
                        "status=not-installed\n"
                        "port=4500\n"
                        "enabled=no\n"
                        "service_name=codex-app-server-prod.service\n"
                        "unit_path=/etc/systemd/system/codex-app-server-prod.service\n"
                        "log_file=/root/.codex/app.log\n"
                        "workspace=/srv/app\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=10,
                    stdout="status=missing-codex\n",
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "status=installed\n"
                        "package_spec=@openai/codex@0.118.0\n"
                        "codex_path=/usr/local/bin/codex\n"
                        "codex_version=codex-cli 0.118.0\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "status=installed\n"
                        "enabled=enabled\n"
                        "service_name=codex-app-server-prod.service\n"
                        "unit_path=/etc/systemd/system/codex-app-server-prod.service\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "runtime=service\n"
                        "status=active\n"
                        "port=4500\n"
                        "enabled=enabled\n"
                        "service_name=codex-app-server-prod.service\n"
                        "unit_path=/etc/systemd/system/codex-app-server-prod.service\n"
                        "log_file=/root/.codex/app.log\n"
                        "workspace=/srv/app\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout="codex-cli 0.118.0\n",
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "status=ok\n"
                        "codex_path=/usr/local/bin/codex\n"
                        "codex_version=codex-cli 0.118.0\n"
                        "auth_status=logged-in\n"
                        "auth_message=Logged in using ChatGPT\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "runtime=service\n"
                        "status=active\n"
                        "port=4500\n"
                        "enabled=enabled\n"
                        "service_name=codex-app-server-prod.service\n"
                        "unit_path=/etc/systemd/system/codex-app-server-prod.service\n"
                        "log_file=/root/.codex/app.log\n"
                        "workspace=/srv/app\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "runtime=service\n"
                        "status=active\n"
                        "port=4500\n"
                        "enabled=enabled\n"
                        "service_name=codex-app-server-prod.service\n"
                        "unit_path=/etc/systemd/system/codex-app-server-prod.service\n"
                        "log_file=/root/.codex/app.log\n"
                        "workspace=/srv/app\n"
                    ),
                    stderr="",
                ),
            ]
        )
        manager = RemoteCodexManager(
            runner=runner,
            wait_for_tunnel_ready=lambda host, port, timeout: None,
        )

        result = manager.up(self._profile(codex_version="0.118.0"), local_port=4903)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["install_action"], "installed")
        self.assertEqual(result["runtime"], "service")
        self.assertEqual(result["remote_codex_version"], "codex-cli 0.118.0")
        self.assertEqual(result["warning"], "")
        self.assertIn("npm install -g @openai/codex@0.118.0", runner.run_calls[3][0][-1])
        self.assertIn("install-service", runner.run_calls[4][0][-1])
        self.assertIn("service-start", runner.run_calls[5][0][-1])

    def test_up_auto_logins_when_configured(self) -> None:
        runner = FakeRunner()
        runner.run_results.extend(
            [
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "status=installed\n"
                        "helper_path=/root/.local/bin/codex-remote-app-server\n"
                        "helper_version=1\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "runtime=service\n"
                        "status=active\n"
                        "port=4500\n"
                        "enabled=enabled\n"
                        "service_name=codex-app-server-prod.service\n"
                        "unit_path=/etc/systemd/system/codex-app-server-prod.service\n"
                        "log_file=/root/.codex/app.log\n"
                        "workspace=/srv/app\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "status=ok\n"
                        "codex_path=/usr/local/bin/codex\n"
                        "codex_version=codex-cli 0.118.0\n"
                        "auth_status=logged-out\n"
                        "auth_message=Not logged in\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "status=ok\n"
                        "codex_path=/usr/local/bin/codex\n"
                        "codex_version=codex-cli 0.118.0\n"
                        "auth_status=logged-in\n"
                        "auth_message=Logged in using API key\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout="codex-cli 0.118.0\n",
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "status=ok\n"
                        "codex_path=/usr/local/bin/codex\n"
                        "codex_version=codex-cli 0.118.0\n"
                        "auth_status=logged-in\n"
                        "auth_message=Logged in using API key\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "runtime=service\n"
                        "status=active\n"
                        "port=4500\n"
                        "enabled=enabled\n"
                        "service_name=codex-app-server-prod.service\n"
                        "unit_path=/etc/systemd/system/codex-app-server-prod.service\n"
                        "log_file=/root/.codex/app.log\n"
                        "workspace=/srv/app\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "runtime=service\n"
                        "status=active\n"
                        "port=4500\n"
                        "enabled=enabled\n"
                        "service_name=codex-app-server-prod.service\n"
                        "unit_path=/etc/systemd/system/codex-app-server-prod.service\n"
                        "log_file=/root/.codex/app.log\n"
                        "workspace=/srv/app\n"
                    ),
                    stderr="",
                ),
            ]
        )
        manager = RemoteCodexManager(
            runner=runner,
            wait_for_tunnel_ready=lambda host, port, timeout: None,
        )

        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}, clear=False):
            result = manager.up(
                self._profile(),
                local_port=4905,
                auto_login=True,
                api_key_env="OPENAI_API_KEY",
            )

        self.assertEqual(result["warning"], "")
        self.assertEqual(result["auth_action"], "logged-in")
        self.assertEqual(runner.run_calls[3][1]["input"], "sk-test")

    def test_up_requires_pinned_version_when_remote_codex_is_missing(self) -> None:
        runner = FakeRunner()
        runner.run_results.extend(
            [
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "status=installed\n"
                        "helper_path=/root/.local/bin/codex-remote-app-server\n"
                        "helper_version=1\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "runtime=service\n"
                        "status=not-installed\n"
                        "port=4500\n"
                        "enabled=no\n"
                        "service_name=codex-app-server-prod.service\n"
                        "unit_path=/etc/systemd/system/codex-app-server-prod.service\n"
                        "log_file=/root/.codex/app.log\n"
                        "workspace=/srv/app\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=10,
                    stdout="status=missing-codex\n",
                    stderr="",
                ),
            ]
        )
        manager = RemoteCodexManager(runner=runner)

        with self.assertRaisesRegex(RuntimeError, "install-codex or save the profile with --codex-version"):
            manager.up(self._profile())

    def test_up_can_sync_local_codex_to_remote_version(self) -> None:
        runner = FakeRunner()
        runner.run_results.extend(
            [
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "status=installed\n"
                        "helper_path=/root/.local/bin/codex-remote-app-server\n"
                        "helper_version=1\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "runtime=service\n"
                        "status=active\n"
                        "port=4500\n"
                        "enabled=enabled\n"
                        "service_name=codex-app-server-prod.service\n"
                        "unit_path=/etc/systemd/system/codex-app-server-prod.service\n"
                        "log_file=/root/.codex/app.log\n"
                        "workspace=/srv/app\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "status=ok\n"
                        "codex_path=/usr/local/bin/codex\n"
                        "codex_version=codex-cli 0.118.0\n"
                        "auth_status=logged-in\n"
                        "auth_message=Logged in using ChatGPT\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout="codex-cli 0.116.0\n",
                    stderr="",
                ),
                subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout="codex-cli 0.118.0\n",
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "status=ok\n"
                        "codex_path=/usr/local/bin/codex\n"
                        "codex_version=codex-cli 0.118.0\n"
                        "auth_status=logged-in\n"
                        "auth_message=Logged in using ChatGPT\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "runtime=service\n"
                        "status=active\n"
                        "port=4500\n"
                        "enabled=enabled\n"
                        "service_name=codex-app-server-prod.service\n"
                        "unit_path=/etc/systemd/system/codex-app-server-prod.service\n"
                        "log_file=/root/.codex/app.log\n"
                        "workspace=/srv/app\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "runtime=service\n"
                        "status=active\n"
                        "port=4500\n"
                        "enabled=enabled\n"
                        "service_name=codex-app-server-prod.service\n"
                        "unit_path=/etc/systemd/system/codex-app-server-prod.service\n"
                        "log_file=/root/.codex/app.log\n"
                        "workspace=/srv/app\n"
                    ),
                    stderr="",
                ),
            ]
        )
        manager = RemoteCodexManager(
            runner=runner,
            wait_for_tunnel_ready=lambda host, port, timeout: None,
        )

        result = manager.up(self._profile(), local_port=4904, sync_local=True)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["local_install_action"], "updated")
        self.assertEqual(result["warning"], "")
        self.assertEqual(result["local_codex_version"], "codex-cli 0.118.0")
        self.assertEqual(
            runner.run_calls[4][0],
            ["npm", "install", "-g", "@openai/codex@0.118.0"],
        )

    def test_up_uses_profile_sync_local_default(self) -> None:
        runner = FakeRunner()
        runner.run_results.extend(
            [
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "status=installed\n"
                        "helper_path=/root/.local/bin/codex-remote-app-server\n"
                        "helper_version=1\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "runtime=service\n"
                        "status=active\n"
                        "port=4500\n"
                        "enabled=enabled\n"
                        "service_name=codex-app-server-prod.service\n"
                        "unit_path=/etc/systemd/system/codex-app-server-prod.service\n"
                        "log_file=/root/.codex/app.log\n"
                        "workspace=/srv/app\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "status=ok\n"
                        "codex_path=/usr/local/bin/codex\n"
                        "codex_version=codex-cli 0.118.0\n"
                        "auth_status=logged-in\n"
                        "auth_message=Logged in using ChatGPT\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout="codex-cli 0.116.0\n",
                    stderr="",
                ),
                subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout="codex-cli 0.118.0\n",
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "status=ok\n"
                        "codex_path=/usr/local/bin/codex\n"
                        "codex_version=codex-cli 0.118.0\n"
                        "auth_status=logged-in\n"
                        "auth_message=Logged in using ChatGPT\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "runtime=service\n"
                        "status=active\n"
                        "port=4500\n"
                        "enabled=enabled\n"
                        "service_name=codex-app-server-prod.service\n"
                        "unit_path=/etc/systemd/system/codex-app-server-prod.service\n"
                        "log_file=/root/.codex/app.log\n"
                        "workspace=/srv/app\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "runtime=service\n"
                        "status=active\n"
                        "port=4500\n"
                        "enabled=enabled\n"
                        "service_name=codex-app-server-prod.service\n"
                        "unit_path=/etc/systemd/system/codex-app-server-prod.service\n"
                        "log_file=/root/.codex/app.log\n"
                        "workspace=/srv/app\n"
                    ),
                    stderr="",
                ),
            ]
        )
        manager = RemoteCodexManager(
            runner=runner,
            wait_for_tunnel_ready=lambda host, port, timeout: None,
        )

        result = manager.up(self._profile(sync_local_on_up=True), local_port=4912)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["local_install_action"], "updated")
        self.assertEqual(
            runner.run_calls[4][0],
            ["npm", "install", "-g", "@openai/codex@0.118.0"],
        )


if __name__ == "__main__":
    unittest.main()
