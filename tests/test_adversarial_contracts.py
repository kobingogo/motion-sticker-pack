from __future__ import annotations

import json
import os
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from config_contract import (  # noqa: E402
    ContractError,
    is_interpreter,
    is_python_interpreter,
    object_sha256,
    validate_provider_config,
    validate_video_task,
)
from execute_video_route import child_environment, diagnostic_tail, execute_attempt  # noqa: E402
from grok_build_video_adapter import (  # noqa: E402
    annotate_error,
    find_grok,
    grok_command,
    parse_structured,
    resolve_grok_home,
)
from manage_job_state import atomic_write, create_state, verify_state  # noqa: E402
from output_safety import prepare_output, validate_archive_name  # noqa: E402
from prompt_compiler import load_tile_plan  # noqa: E402
from render_keypose_pack import natural_key  # noqa: E402
from route_video_provider import route  # noqa: E402


def base_config() -> dict:
    return {
        "version": 1,
        "routing": {"policy": "local-first", "max_attempts": 2, "fallback": "keypose-local"},
        "providers": [],
    }


class AdversarialContractTests(unittest.TestCase):
    def test_shipped_provider_example_satisfies_runtime_contract(self) -> None:
        example = Path(__file__).resolve().parents[1] / "assets" / "video-providers.example.json"
        validate_provider_config(json.loads(example.read_text(encoding="utf-8")))

    def test_duplicate_provider_ids_are_rejected(self) -> None:
        config = base_config()
        provider = {
            "id": "native",
            "driver": "native-tool",
            "enabled": True,
            "priority": 1,
            "tool": "video",
            "capabilities": ["image-to-video"],
        }
        config["providers"] = [provider, dict(provider)]
        with self.assertRaisesRegex(ContractError, "duplicate provider"):
            validate_provider_config(config)

    def test_windows_and_py_launcher_names_count_as_interpreters(self) -> None:
        for name in (
            "python",
            "python3",
            "python3.12",
            "py",
            "python.exe",
            "python3.exe",
            "python3.12.exe",
            "py.exe",
            "C:\\Python312\\python.exe",
            "/usr/bin/python3",
            "node.exe",
        ):
            with self.subTest(name=name):
                self.assertTrue(is_interpreter(name))
        self.assertTrue(is_python_interpreter("py.exe"))
        self.assertTrue(is_python_interpreter(r"C:\Users\me\AppData\Local\Programs\Python\Python312\python.exe"))
        self.assertFalse(is_python_interpreter("node.exe"))
        self.assertFalse(is_interpreter("grok.exe"))

    def test_python_exe_command_requires_absolute_entrypoint(self) -> None:
        config = base_config()
        config["providers"] = [
            {
                "id": "relay",
                "driver": "command",
                "enabled": False,
                "priority": 1,
                "command": ["python.exe", "adapter.py"],
                "capabilities": ["image-to-video"],
            }
        ]
        with self.assertRaisesRegex(ContractError, "absolute path"):
            validate_provider_config(config)

    def test_adapter_inherits_windows_runtime_variables_without_secrets(self) -> None:
        provider = {"credentials": {"env": ["XAI_API_KEY"]}}
        child = child_environment(
            provider,
            {
                "PATH": "/bin",
                "SYSTEMROOT": r"C:\Windows",
                "USERPROFILE": r"C:\Users\bingo",
                "XAI_API_KEY": "allowed",
                "AWS_SECRET_ACCESS_KEY": "blocked",
            },
        )
        self.assertEqual(child["SYSTEMROOT"], r"C:\Windows")
        self.assertEqual(child["USERPROFILE"], r"C:\Users\bingo")
        self.assertEqual(child["XAI_API_KEY"], "allowed")
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", child)

    def test_windows_env_keys_are_matched_case_insensitively(self) -> None:
        with patch("execute_video_route.os.name", "nt"):
            child = child_environment(
                {"credentials": {"env": []}},
                {
                    "Path": r"C:\Windows\System32",
                    "SystemRoot": r"C:\Windows",
                    "AWS_SECRET_ACCESS_KEY": "blocked",
                },
            )
        self.assertEqual(child["Path"], r"C:\Windows\System32")
        self.assertEqual(child["SystemRoot"], r"C:\Windows")
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", child)

    def test_find_grok_accepts_exe_under_grok_home_bin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            binary = home / "bin" / "grok.exe"
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"")
            binary.chmod(0o755)
            found = find_grok({"GROK_HOME": str(home), "PATH": str(home / "missing")})
            self.assertEqual(Path(found), binary.resolve())

    def test_find_grok_honors_grok_bin_even_when_named_exe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "custom-grok.exe"
            binary.write_bytes(b"")
            binary.chmod(0o755)
            found = find_grok({"GROK_BIN": str(binary), "GROK_HOME": str(Path(temporary) / "empty"), "PATH": ""})
            self.assertEqual(Path(found), binary.resolve())

    def test_adapter_inherits_only_declared_credential(self) -> None:
        provider = {"credentials": {"env": ["XAI_API_KEY"]}}
        child = child_environment(
            provider,
            {
                "PATH": "/bin",
                "USER": "bingo",
                "XAI_API_KEY": "allowed",
                "AWS_SECRET_ACCESS_KEY": "blocked",
            },
        )
        self.assertEqual(child, {"PATH": "/bin", "USER": "bingo", "XAI_API_KEY": "allowed"})

    def test_adapter_inherits_present_optional_environment_without_making_it_required(self) -> None:
        provider = {
            "credentials": {
                "env": ["XAI_API_KEY"],
                "optional_env": ["XAI_VIDEO_UPLOAD_URL"],
            }
        }
        child = child_environment(
            provider,
            {
                "PATH": "/bin",
                "XAI_API_KEY": "allowed",
                "XAI_VIDEO_UPLOAD_URL": "https://upload.invalid/video.mp4",
                "AWS_SECRET_ACCESS_KEY": "blocked",
            },
        )
        self.assertEqual(
            child,
            {
                "PATH": "/bin",
                "XAI_API_KEY": "allowed",
                "XAI_VIDEO_UPLOAD_URL": "https://upload.invalid/video.mp4",
            },
        )

    def test_required_and_optional_environment_must_not_overlap(self) -> None:
        config = base_config()
        config["providers"] = [
            {
                "id": "relay",
                "driver": "command",
                "enabled": False,
                "priority": 1,
                "command": [sys.executable, str((SCRIPTS / "xai_rest_video_adapter.py").resolve())],
                "credentials": {"env": ["XAI_API_KEY"], "optional_env": ["XAI_API_KEY"]},
                "capabilities": ["image-to-video"],
            }
        ]
        with self.assertRaisesRegex(ContractError, "must not overlap"):
            validate_provider_config(config)

    def test_diagnostic_tail_redacts_bearer_and_api_key_values(self) -> None:
        diagnostic = diagnostic_tail(
            b"Authorization: Bearer secret-token\napi_key=another-secret\nZero Data Retention requires output.upload_url"
        )
        self.assertNotIn("secret-token", diagnostic)
        self.assertNotIn("another-secret", diagnostic)
        self.assertIn("output.upload_url", diagnostic)

    def test_grok_structured_output_accepts_string_or_object(self) -> None:
        expected = {"status": "ok", "output": "/tmp/video.mp4"}
        for field, structured in (
            ("structuredOutput", expected),
            ("structuredOutput", json.dumps(expected)),
            ("text", json.dumps(expected)),
        ):
            with self.subTest(field=field, structured=structured):
                outer = json.dumps({field: structured}).encode()
                self.assertEqual(parse_structured(outer), expected)

    def test_grok_structured_output_recovers_last_json_after_progress_text(self) -> None:
        expected = {"status": "failed", "message": "provider failure"}
        outer = json.dumps(
            {"text": "Starting the requested tool call.\n" + json.dumps(expected)}
        ).encode()
        self.assertEqual(parse_structured(outer), expected)

    def test_grok_home_and_leader_socket_stay_isolated(self) -> None:
        isolated = Path("/tmp/motion-sticker-grok-home")
        home = resolve_grok_home({"GROK_HOME": str(isolated)})
        command = grok_command(
            "/usr/bin/grok",
            "generate",
            Path("/tmp/raw-video"),
            home,
            {"GROK_DEBUG_FILE": "/tmp/grok-debug.log"},
        )
        self.assertEqual(home, isolated.resolve())
        self.assertIn("--verbatim", command)
        self.assertIn("--leader-socket", command)
        self.assertEqual(command[command.index("--leader-socket") + 1], str(home / "leader.sock"))
        self.assertEqual(command[command.index("--debug-file") + 1], "/tmp/grok-debug.log")

    def test_zdr_errors_include_storage_configuration_hint(self) -> None:
        message = annotate_error(
            "Video generation tools are unavailable under zero data retention (ZDR)."
        )
        self.assertIn("managed_config.toml", message)
        self.assertIn("docs.x.ai/build/settings/zdr-video-storage", message)

    def test_command_adapter_executes_one_hash_bound_route_with_filtered_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "sheet.png"
            layout = root / "layout.json"
            static_prompt = root / "static-prompt.json"
            prompts = root / "prompts.json"
            state_path = root / "job-state.json"
            raw = root / "raw"
            raw.mkdir()
            image.write_bytes(b"approved image")
            layout_value = {"detected_layout": {"columns": 1, "rows": 1, "count": 1, "confidence": 0.99}}
            layout.write_text(json.dumps(layout_value), encoding="utf-8")
            static_prompt.write_text(json.dumps({"static_sheet_prompt": "static"}), encoding="utf-8")
            prompts.write_text(
                json.dumps({**layout_value, "grid_video_prompt": "small independent loop"}),
                encoding="utf-8",
            )
            state = create_state(image, layout, static_prompt, "generated")
            state["phase"] = "static-approved"
            state["approval"] = {
                "kind": "explicit-user-confirmation",
                "approved_at": "2026-08-26T00:00:00Z",
                "static_sha256": state["static_image"]["sha256"],
            }
            atomic_write(state_path, state)

            adapter = root / "adapter.py"
            adapter.write_text(
                textwrap.dedent(
                    """
                    import argparse, json, os
                    from pathlib import Path
                    parser = argparse.ArgumentParser()
                    parser.add_argument('--task', required=True)
                    parser.add_argument('--output', required=True)
                    args = parser.parse_args()
                    task = json.loads(Path(args.task).read_text())
                    video = Path(task['output_directory']) / 'fake.mp4'
                    video.write_bytes(b'fake video')
                    Path(args.output).write_text(json.dumps({
                        'status': 'succeeded',
                        'provider': 'relay',
                        'output': str(video.resolve()),
                        'seen_env': sorted(key for key in os.environ if key.endswith('_KEY')),
                    }))
                    """
                ),
                encoding="utf-8",
            )
            config = base_config()
            config["providers"] = [
                {
                    "id": "relay",
                    "driver": "command",
                    "provider": "custom",
                    "model": "fake",
                    "enabled": True,
                    "priority": 50,
                    "command": [sys.executable, str(adapter.resolve())],
                    "credentials": {"env": ["VIDEO_RELAY_API_KEY"]},
                    "capabilities": ["image-to-video"],
                }
            ]
            task = {
                "version": 1,
                "operation": "image-to-video",
                "required_capabilities": ["image-to-video"],
                "provider": "relay",
                "allow_fallback": False,
                "input_image": str(image.resolve()),
                "layout_file": str(layout.resolve()),
                "prompt_file": str(prompts.resolve()),
                "approval_file": str(state_path.resolve()),
                "output_directory": str(raw.resolve()),
            }
            capability_report = {
                "version": 1,
                "config_sha256": object_sha256(config),
                "providers": [
                    {
                        "id": "relay",
                        "driver": "command",
                        "available": True,
                        "priority": 50,
                        "capabilities": ["image-to-video"],
                    }
                ],
                "local_processing": {},
            }
            route_report = route(config, capability_report, task)
            config_path = root / "providers.json"
            task_path = root / "task.json"
            result_path = root / "result.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            task_path.write_text(json.dumps(task), encoding="utf-8")
            with patch.dict(
                os.environ,
                {"VIDEO_RELAY_API_KEY": "allowed", "AWS_SECRET_ACCESS_KEY": "must-not-leak"},
                clear=False,
            ):
                execute_attempt(config_path, task_path, route_report, result_path, 1)
            result = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertIn("VIDEO_RELAY_API_KEY", result["seen_env"])
            self.assertNotIn("AWS_SECRET_ACCESS_KEY", result["seen_env"])

    def test_interpreter_adapter_requires_absolute_entrypoint(self) -> None:
        config = base_config()
        config["providers"] = [
            {
                "id": "relay",
                "driver": "command",
                "enabled": False,
                "priority": 1,
                "command": ["node", "relative-adapter.mjs"],
                "capabilities": ["image-to-video"],
            }
        ]
        with self.assertRaisesRegex(ContractError, "absolute path"):
            validate_provider_config(config)

    def test_literal_secret_in_provider_options_is_rejected(self) -> None:
        config = base_config()
        config["providers"] = [
            {
                "id": "xai",
                "driver": "ai-sdk",
                "enabled": True,
                "priority": 1,
                "provider": "xai",
                "package": "@ai-sdk/xai",
                "model": "grok-imagine-video",
                "capabilities": ["image-to-video"],
                "credentials": {"env": ["XAI_API_KEY"]},
                "provider_options": {"apiKey": "must-not-be-here"},
            }
        ]
        with self.assertRaisesRegex(ContractError, "secret-like"):
            validate_provider_config(config)

    def test_unknown_provider_option_is_rejected_before_execution(self) -> None:
        config = base_config()
        config["providers"] = [
            {
                "id": "seedance",
                "driver": "ai-sdk",
                "enabled": True,
                "priority": 1,
                "provider": "bytedance",
                "package": "@ai-sdk/bytedance",
                "model": "seedance-1-5-pro-251215",
                "capabilities": ["image-to-video"],
                "credentials": {"env": ["ARK_API_KEY"]},
                "provider_options": {"cameraFiexed": True},
            }
        ]
        with self.assertRaisesRegex(ContractError, "unsupported fields"):
            validate_provider_config(config)

    def test_ai_sdk_auth_alias_region_and_i2v_model_are_validated(self) -> None:
        config = base_config()
        config["providers"] = [
            {
                "id": "wan",
                "driver": "ai-sdk",
                "enabled": True,
                "priority": 1,
                "provider": "alibaba",
                "package": "@ai-sdk/alibaba",
                "model": "wan2.6-i2v-flash",
                "region": "china",
                "capabilities": ["image-to-video"],
                "credentials": {"env": ["DASHSCOPE_API_KEY"]},
            }
        ]
        self.assertEqual(validate_provider_config(config), config)

        config["providers"][0]["credentials"] = {"env": ["UNRELATED_API_KEY"]}
        with self.assertRaisesRegex(ContractError, "supported authentication set"):
            validate_provider_config(config)

        config["providers"][0]["credentials"] = {"env": ["ALIBABA_API_KEY"]}
        config["providers"][0]["region"] = "global"
        with self.assertRaisesRegex(ContractError, "region must be one of"):
            validate_provider_config(config)

        config["providers"][0]["region"] = "international"
        config["providers"][0]["model"] = "wan2.6-t2v"
        with self.assertRaisesRegex(ContractError, "not an image-to-video model"):
            validate_provider_config(config)

    def test_video_task_polling_and_retry_limits_are_validated(self) -> None:
        task = {
            "version": 1,
            "operation": "image-to-video",
            "input_image": "/tmp/sheet.png",
            "layout_file": "/tmp/layout.json",
            "prompt_file": "/tmp/prompts.json",
            "approval_file": "/tmp/job-state.json",
            "output_directory": "/tmp/raw",
            "poll_interval_ms": 100,
            "max_retries": 0,
        }
        self.assertEqual(validate_video_task(task, require_execution_fields=True), task)
        for field, value, message in (
            ("poll_interval_ms", 99, "poll_interval_ms"),
            ("max_retries", 4, "max_retries"),
        ):
            invalid = dict(task)
            invalid[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ContractError, message):
                validate_video_task(invalid, require_execution_fields=True)

    def test_stale_capability_report_is_rejected(self) -> None:
        config = base_config()
        report = {
            "version": 1,
            "config_sha256": "0" * 64,
            "providers": [],
            "local_processing": {},
        }
        with self.assertRaisesRegex(ValueError, "different provider config"):
            route(config, report, {"version": 1, "operation": "image-to-video"})

    def test_forged_external_provider_is_not_routable(self) -> None:
        config = base_config()
        report = {
            "version": 1,
            "config_sha256": object_sha256(config),
            "providers": [
                {
                    "id": "forged",
                    "driver": "ai-sdk",
                    "available": True,
                    "priority": 100,
                    "capabilities": ["image-to-video"],
                }
            ],
            "local_processing": {},
        }
        result = route(config, report, {"version": 1, "operation": "image-to-video", "allow_fallback": False})
        self.assertIsNone(result["selected"])
        self.assertEqual(result["rejected"][0]["reason"], "not-enabled-in-provider-config")

    def test_regenerated_image_invalidates_approval_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "sheet.png"
            layout = root / "layout.json"
            image.write_bytes(b"first revision")
            layout.write_text(
                json.dumps({"detected_layout": {"columns": 3, "rows": 3, "count": 9, "confidence": 0.95}})
            )
            state = create_state(image, layout, None, "user-supplied")
            self.assertTrue(verify_state(state, image, layout)["valid"])
            image.write_bytes(b"second revision")
            with self.assertRaisesRegex(ValueError, "does not match"):
                verify_state(state, image, layout)

    def test_archive_path_traversal_is_rejected(self) -> None:
        for name in ("../pack.zip", "/tmp/pack.zip", "pack.tar"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_archive_name(name)

    def test_stale_outputs_require_explicit_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            stale = output / "12.webp"
            stale_gif = output / "12.gif"
            stale.write_bytes(b"stale")
            stale_gif.write_bytes(b"stale-gif")
            with self.assertRaises(FileExistsError):
                prepare_output(output, overwrite=False)
            prepare_output(output, overwrite=True)
            self.assertFalse(stale.exists())
            self.assertFalse(stale_gif.exists())

    def test_generic_motion_requires_explicit_opt_in(self) -> None:
        with self.assertRaisesRegex(ValueError, "tile-plan"):
            load_tile_plan(None, 9)
        self.assertEqual(len(load_tile_plan(None, 9, allow_generic=True)), 9)

    def test_numeric_keypose_sorting(self) -> None:
        ordered = sorted((Path("10"), Path("2"), Path("01")), key=natural_key)
        self.assertEqual([item.name for item in ordered], ["01", "2", "10"])


if __name__ == "__main__":
    unittest.main()
