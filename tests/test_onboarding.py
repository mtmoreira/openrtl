"""Local onboarding behavior; these tests do not qualify an RTL-design model."""

from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from openrtl.onboarding import (
    AGENTRIG_VERSION, CONFIG_NAME, DEFAULT_MODEL, MAX_CONFIG_BYTES, OLLAMA_SDK_VERSION, OPENAI_SDK_VERSION,
    SetupConfig, SetupError,
    default_state_dir, guided_setup, load_config, main, readiness, save_config, validate_config,
)


class OnboardingStorageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        # macOS's temporary-directory aliases must not become symlink exceptions.
        self.root = Path(self.temporary.name).resolve()
        self.state = self.root / "private product state"

    def test_round_trip_uses_private_atomic_state_and_preserves_other_files(self) -> None:
        config = SetupConfig(model="explicit-model", credential_env="RTL_PROVIDER_KEY", max_calls=7)
        save_config(self.state, config)
        sentinel = self.state / "keep.txt"
        sentinel.write_text("retained unrelated evidence")
        self.assertEqual(load_config(self.state), config)
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.state / CONFIG_NAME).stat().st_mode), 0o600)
        replacement = replace(config, max_calls=8)
        save_config(self.state, replacement)
        self.assertEqual(load_config(self.state), replacement)
        self.assertEqual(sentinel.read_text(), "retained unrelated evidence")
        self.assertEqual(sorted(path.name for path in self.state.iterdir()), ["keep.txt", CONFIG_NAME])

    def test_interrupted_replace_keeps_previous_complete_configuration(self) -> None:
        original = SetupConfig(model="chosen-model")
        save_config(self.state, original)
        with patch("openrtl.onboarding.os.replace", side_effect=OSError("synthetic interruption")):
            with self.assertRaises(OSError):
                save_config(self.state, replace(original, max_calls=9))
        self.assertEqual(load_config(self.state), original)
        self.assertEqual(list(path.name for path in self.state.iterdir()), [CONFIG_NAME])
        save_config(self.state, replace(original, max_calls=9))
        self.assertEqual(load_config(self.state), replace(original, max_calls=9))

    def test_symlink_parent_config_and_hardlink_are_rejected_without_target_change(self) -> None:
        actual = self.root / "actual"
        actual.mkdir(mode=0o700)
        alias = self.root / "alias"
        alias.symlink_to(actual, target_is_directory=True)
        with self.assertRaises(OSError):
            save_config(alias / "state", SetupConfig())
        self.assertEqual(list(actual.iterdir()), [])
        save_config(self.state, SetupConfig())
        target = self.state / CONFIG_NAME
        retained = self.root / "retained.json"
        target.rename(retained)
        before = retained.read_bytes()
        target.symlink_to(retained)
        with self.assertRaises((OSError, SetupError)):
            load_config(self.state)
        with self.assertRaises(SetupError):
            save_config(self.state, SetupConfig())
        target.unlink()
        os.link(retained, target)
        with self.assertRaises(SetupError):
            load_config(self.state)
        with self.assertRaises(SetupError):
            save_config(self.state, SetupConfig())
        self.assertEqual(retained.read_bytes(), before)

    def test_unrelated_insecure_directory_is_not_chmodded(self) -> None:
        self.state.mkdir(mode=0o755)
        self.state.chmod(0o755)
        with self.assertRaises(SetupError):
            save_config(self.state, SetupConfig())
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o755)
        self.assertEqual(list(self.state.iterdir()), [])

    def test_non_regular_and_oversized_config_fail_without_blocking(self) -> None:
        self.state.mkdir(mode=0o700)
        target = self.state / CONFIG_NAME
        os.mkfifo(target, mode=0o600)
        with self.assertRaises(SetupError):
            load_config(self.state)
        target.unlink()
        target.write_bytes(b" " * (MAX_CONFIG_BYTES + 1))
        target.chmod(0o600)
        with self.assertRaisesRegex(SetupError, "too_large"):
            load_config(self.state)

    def test_missing_state_is_read_only_and_corrupt_state_is_not_overwritten(self) -> None:
        self.assertIsNone(load_config(self.state))
        self.assertFalse(self.state.exists())
        self.state.mkdir(mode=0o700)
        target = self.state / CONFIG_NAME
        target.write_bytes(b'{"schema":')
        target.chmod(0o600)
        with patch("sys.stdin.isatty", return_value=False), redirect_stdout(io.StringIO()):
            self.assertEqual(main(["setup", "--noninteractive", "--state-dir", str(self.state)]), 2)
        self.assertEqual(target.read_bytes(), b'{"schema":')

    def test_traversal_and_insecure_config_are_rejected(self) -> None:
        with self.assertRaisesRegex(SetupError, "directory_invalid"):
            save_config(self.root / "a" / ".." / "b", SetupConfig())
        save_config(self.state, SetupConfig())
        (self.state / CONFIG_NAME).chmod(0o644)
        with self.assertRaises(SetupError):
            load_config(self.state)


class OnboardingConfigurationTest(unittest.TestCase):
    def test_saved_configuration_cannot_contain_credentials_or_authorization(self) -> None:
        valid = SetupConfig().document()
        self.assertEqual(validate_config(valid), SetupConfig())
        for key in ("api_key", "allow_provider", "allow_simulation", "allow_downloads", "simulation_profile"):
            with self.subTest(key=key), self.assertRaises(SetupError):
                validate_config({**valid, key: True})
        for key, value in (("model", "sk-synthetic-do-not-save"), ("model", "model\ntext"),
                           ("credential_env", "synthetic-key-value"), ("credential_env", "OPENAI_API_KEY=value"),
                           ("max_calls", True), ("max_repairs", -1), ("max_calls", 201),
                           ("max_output_tokens", 255), ("timeout_seconds", 301), ("model", 4)):
            with self.subTest(key=key, value=value), self.assertRaises(SetupError):
                validate_config({**valid, key: value})

    def test_model_choice_is_explicit_and_existing_choice_is_never_upgraded(self) -> None:
        self.assertEqual(SetupConfig().model, DEFAULT_MODEL)
        previous = SetupConfig(model="chosen-explicit-model", max_calls=12)
        messages: list[str] = []
        blank = lambda prompt: ""
        selected = guided_setup(previous, read=blank, emit=messages.append)
        self.assertEqual(selected, previous)
        self.assertTrue(any("never upgrades" in message for message in messages))
        self.assertTrue(any("does not authorize" in message for message in messages))
        answers = iter(("other-model", "RTL_KEY", "6", "1", "4000", "60"))
        selected = guided_setup(previous, read=lambda prompt: next(answers), emit=messages.append)
        self.assertEqual(selected, SetupConfig("other-model", "RTL_KEY", 6, 1, 4000, 60))

    def test_readiness_inspects_metadata_only_and_retains_pending_gates(self) -> None:
        versions = {"openrtl": "0.4.0", "agentrig": AGENTRIG_VERSION,
                    "openai": OPENAI_SDK_VERSION, "ollama": OLLAMA_SDK_VERSION}
        with patch("openrtl.onboarding.version", side_effect=versions.__getitem__), \
                patch("openrtl.onboarding.sys.version_info", (3, 12, 0)), \
                patch.dict(os.environ, {"SYNTHETIC_RTL_KEY": "synthetic-never-read-value"}):
            with patch.object(type(os.environ), "__getitem__", side_effect=AssertionError("must not resolve environment")):
                report = readiness(SetupConfig(credential_env="SYNTHETIC_RTL_KEY"))
        self.assertFalse(report["provider_calls"])
        self.assertFalse(report["credential_resolution"])
        self.assertFalse(report["simulation_performed"])
        self.assertTrue(report["local_review_ready"])
        self.assertTrue(report["provider_configuration_ready"])
        self.assertFalse(report["provider_authorized"])
        self.assertFalse(report["simulation_ready"])
        self.assertFalse(report["live_qualified"])
        self.assertFalse(report["release_qualified"])
        self.assertIn("unconfigured", str(report["simulation"]))
        self.assertIn("M46 pending", str(report["live_qualification"]))
        self.assertEqual(report["release"], "M47 pending")
        self.assertNotIn("synthetic-never-read-value", json.dumps(report))

    def test_readiness_requires_exact_dependency_versions_and_never_infers_authority(self) -> None:
        for agentrig_version, openai_version, ollama_version, python_version, local_ready, configured in (
            (AGENTRIG_VERSION, OPENAI_SDK_VERSION, OLLAMA_SDK_VERSION, (3, 12, 0), True, True),
            ("0.3.1", OPENAI_SDK_VERSION, OLLAMA_SDK_VERSION, (3, 12, 0), False, False),
            (AGENTRIG_VERSION, "2.48.0", OLLAMA_SDK_VERSION, (3, 12, 0), True, False),
            (AGENTRIG_VERSION, OPENAI_SDK_VERSION, "0.6.1", (3, 12, 0), True, False),
            (AGENTRIG_VERSION, OPENAI_SDK_VERSION, OLLAMA_SDK_VERSION, (3, 11, 0), False, False),
        ):
            versions = {"openrtl": "0.4.0", "agentrig": agentrig_version,
                        "openai": openai_version, "ollama": ollama_version}
            with self.subTest(versions=versions, python=python_version), \
                    patch("openrtl.onboarding.version", side_effect=versions.__getitem__), \
                    patch("openrtl.onboarding.sys.version_info", python_version):
                report = readiness(SetupConfig())
            self.assertEqual(report["local_review_ready"], local_ready)
            self.assertEqual(report["provider_configuration_ready"], configured)
            self.assertFalse(report["provider_authorized"])
            if openai_version != OPENAI_SDK_VERSION or ollama_version != OLLAMA_SDK_VERSION:
                self.assertIn(OPENAI_SDK_VERSION, str(report["optional_provider_sdk"]))
                self.assertIn(OLLAMA_SDK_VERSION, str(report["optional_provider_sdk"]))
                self.assertIn("separate approval", str(report["optional_provider_sdk"]))

    def test_default_paths_are_product_scoped_and_relative_xdg_is_rejected(self) -> None:
        with patch("openrtl.onboarding.sys.platform", "darwin"), patch("openrtl.onboarding.Path.home", return_value=Path("/home/test")):
            self.assertEqual(default_state_dir(), Path("/home/test/Library/Application Support/OpenRTL"))
        with patch("openrtl.onboarding.sys.platform", "linux"), patch.dict(os.environ, {"XDG_STATE_HOME": "/state"}):
            self.assertEqual(default_state_dir(), Path("/state/openrtl"))
        with patch("openrtl.onboarding.sys.platform", "linux"), patch.dict(os.environ, {"XDG_STATE_HOME": "relative"}):
            with self.assertRaises(SetupError):
                default_state_dir()


class OnboardingFrontDoorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state = Path(self.temporary.name).resolve() / "state with spaces"

    def test_batch_and_existing_commands_never_prompt_or_read_setup(self) -> None:
        for arguments in (["batch", "--project", "project with spaces", "--delegation", "delegation.json"],
                          ["chat", "--project", "project", "--allow-provider", "--model", "explicit"],
                          ["acceptance", "--project", "project"]):
            with self.subTest(arguments=arguments), \
                    patch("openrtl.onboarding._forward", return_value=9) as forward, \
                    patch("builtins.input", side_effect=AssertionError("must not prompt")), \
                    patch("openrtl.onboarding.load_config", side_effect=AssertionError("must not read setup")):
                self.assertEqual(main(arguments), 9)
                forward.assert_called_once_with(arguments)

    def test_noninteractive_noargs_stops_without_writes_or_prompts(self) -> None:
        with patch("sys.stdin.isatty", return_value=False), \
                patch("builtins.input", side_effect=AssertionError("must not prompt")), \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["--state-dir", str(self.state)]), 2)
        self.assertFalse(self.state.exists())
        self.assertIn("--noninteractive", output.getvalue())

    def test_batch_with_front_door_state_option_still_never_inspects_setup(self) -> None:
        arguments = ["batch", "--project", "explicit-project", "--delegation", "delegation.json"]
        with patch("openrtl.onboarding._forward", return_value=2) as forward, \
                patch("openrtl.onboarding.load_config", side_effect=AssertionError("must not read setup")), \
                patch("builtins.input", side_effect=AssertionError("must not prompt")):
            self.assertEqual(main(["--state-dir", str(self.state), *arguments]), 2)
        forward.assert_called_once_with(arguments)
        self.assertFalse(self.state.exists())

    def test_explicit_noninteractive_setup_is_resumable_and_keeps_existing_model(self) -> None:
        with patch("sys.stdin.isatty", return_value=False), \
                patch("builtins.input", side_effect=AssertionError("must not prompt")), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(main(["setup", "--state-dir", str(self.state), "--noninteractive",
                                   "--model", "my-model", "--max-calls", "6"]), 0)
            self.assertEqual(main(["--state-dir", str(self.state), "setup", "--noninteractive",
                                   "--max-repairs", "1"]), 0)
        self.assertEqual(load_config(self.state), SetupConfig(model="my-model", max_calls=6, max_repairs=1))
        self.assertEqual(set(json.loads((self.state / CONFIG_NAME).read_bytes())), set(SetupConfig().document()))

    def test_first_run_and_resume_use_existing_cli_without_restoring_authority(self) -> None:
        answers = ["", "", "", "", "", "", "my-project"]
        with redirect_stdout(io.StringIO()), patch("sys.stdin.isatty", return_value=True), patch("sys.stdout.isatty", return_value=True), \
                patch("builtins.input", side_effect=answers), \
                patch("openrtl.onboarding._forward", return_value=0) as forward:
            self.assertEqual(main(["--state-dir", str(self.state)]), 0)
        assert forward.call_args is not None
        arguments = forward.call_args.args[0]
        self.assertEqual(arguments[0], "chat")
        self.assertEqual(arguments[arguments.index("--model") + 1], DEFAULT_MODEL)
        self.assertNotIn("--allow-provider", arguments)
        self.assertNotIn("--allow-simulation", arguments)
        project = self.state / "projects" / "my-project"
        self.assertEqual(arguments[arguments.index("--project") + 1], str(project))
        project.mkdir(mode=0o700)
        with redirect_stdout(io.StringIO()), patch("sys.stdin.isatty", return_value=True), patch("sys.stdout.isatty", return_value=True), \
                patch("builtins.input", return_value="my-project") as prompt, \
                patch("openrtl.onboarding._forward", return_value=0) as forward:
            self.assertEqual(main(["--state-dir", str(self.state)]), 0)
        self.assertEqual(prompt.call_count, 1)
        assert forward.call_args is not None
        self.assertEqual(forward.call_args.args[0][0], "resume")
        self.assertNotIn("--allow-provider", forward.call_args.args[0])

    def test_local_specification_review_reaches_real_session_without_provider(self) -> None:
        from openrtl.adapters.design_session_store import DesignSessionStore
        from openrtl.application.design_agent import DesignAgent
        from openrtl.design_cli import conversation as real_conversation
        from tests.test_design_agent import specification

        spec = specification()
        spec_path = self.state.parent / "local-spec.json"
        spec_path.write_text(json.dumps(spec))
        # This synthetic spec verifies local review and persistence only. It is
        # not generated RTL and provides no live-model qualification evidence.
        answers = ["", "", "", "", "", "", "local-review"]
        commands = iter(("/spec " + str(spec_path), "/show", "/quit"))

        async def review(agent: DesignAgent) -> int:
            # Input's default is bound at import, so inject only the local
            # command reader and preserve the real CLI/store/agent behavior.
            return await real_conversation(agent, read=lambda prompt: next(commands))

        with redirect_stdout(io.StringIO()) as output, \
                patch("sys.stdin.isatty", return_value=True), patch("sys.stdout.isatty", return_value=True), \
                patch("builtins.input", side_effect=answers), \
                patch("openrtl.design_cli.conversation", new=review):
            self.assertEqual(main(["--state-dir", str(self.state)]), 0)
        self.assertIn("State: discovery", output.getvalue())
        store = DesignSessionStore(self.state / "projects" / "local-review", read_only=True)
        try:
            self.assertEqual(store.read()["spec"], spec)
            self.assertEqual(store.read()["calls"], 0)
            self.assertIsNone(store.read()["simulation"])
        finally:
            store.close()

    def test_interruption_before_save_leaves_no_setup_and_can_be_retried(self) -> None:
        with redirect_stdout(io.StringIO()), patch("sys.stdin.isatty", return_value=True), patch("sys.stdout.isatty", return_value=True), \
                patch("builtins.input", side_effect=EOFError):
            self.assertEqual(main(["--state-dir", str(self.state)]), 130)
        self.assertFalse(self.state.exists())
        with patch("sys.stdin.isatty", return_value=False), redirect_stdout(io.StringIO()):
            self.assertEqual(main(["setup", "--state-dir", str(self.state), "--noninteractive"]), 0)

    def test_doctor_is_read_only_noninteractive_and_provider_free(self) -> None:
        with patch("sys.stdin.isatty", return_value=False), \
                patch("builtins.input", side_effect=AssertionError("must not prompt")), \
                patch("openrtl.onboarding._forward", side_effect=AssertionError("must not dispatch")), \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["doctor", "--state-dir", str(self.state), "--json"]), 0)
        report = json.loads(output.getvalue())
        self.assertFalse(report["credential_resolution"])
        self.assertIn("not configured", report["setup"])
        self.assertFalse(self.state.exists())

    def test_doctor_default_success_is_diagnostic_and_require_local_enforces_readiness(self) -> None:
        with patch("openrtl.onboarding.version", return_value="unexpected-version"), \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["doctor", "--state-dir", str(self.state), "--json"]), 0)
        self.assertFalse(json.loads(output.getvalue())["local_review_ready"])
        with patch("openrtl.onboarding.version", return_value="unexpected-version"), \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["doctor", "--state-dir", str(self.state), "--require-local", "--json"]), 2)
        self.assertFalse(json.loads(output.getvalue())["local_review_ready"])
        self.assertFalse(self.state.exists())


if __name__ == "__main__":
    unittest.main()
