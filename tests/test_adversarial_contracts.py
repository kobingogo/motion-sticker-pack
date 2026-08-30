from __future__ import annotations

import atexit
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
from artifact_manifest import record_artifact, verify_manifest  # noqa: E402
from attempt_ledger import (  # noqa: E402
    archive_ledger,
    atomic_write_json,
    claim_attempt,
    finish_attempt,
    ledger_lock,
    progress_path_for,
)
from execute_video_route import child_environment, diagnostic_tail, execute_attempt  # noqa: E402
from grok_build_video_adapter import (  # noqa: E402
    annotate_error,
    build_instruction,
    compact_motion_prompt,
    find_grok,
    grok_command,
    grok_session_videos,
    parse_structured,
    promote_accepted_video,
    resolve_grok_home,
)
from manage_job_state import atomic_write, create_state, verify_state  # noqa: E402
from output_safety import (  # noqa: E402
    begin_output_transaction,
    prepare_output,
    validate_archive_name,
    validate_output_boundaries,
)
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
    def test_output_transaction_rolls_back_and_preserves_prior_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "pack"
            output.mkdir()
            (output / "01.png").write_bytes(b"old")
            (output / "keep.txt").write_bytes(b"user")
            transaction = begin_output_transaction(output, overwrite=True)
            self.assertFalse((output / "01.png").exists())
            self.assertEqual((output / "keep.txt").read_bytes(), b"user")
            (output / "01.png").write_bytes(b"partial")
            transaction.rollback()
            self.assertEqual((output / "01.png").read_bytes(), b"old")
            self.assertEqual((output / "keep.txt").read_bytes(), b"user")

    def test_output_transaction_recovers_abandoned_run_before_new_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "pack"
            output.mkdir()
            (output / "01.png").write_bytes(b"old")
            abandoned = begin_output_transaction(output, overwrite=True)
            (output / "01.png").write_bytes(b"partial")
            atexit.unregister(abandoned.rollback)
            abandoned.started = False
            journal = json.loads(abandoned.journal.read_text(encoding="utf-8"))
            journal["pid"] = -1
            abandoned.journal.write_text(json.dumps(journal), encoding="utf-8")
            recovered = begin_output_transaction(output, overwrite=True)
            recovered.rollback()
            self.assertEqual((output / "01.png").read_bytes(), b"old")

    def test_output_transaction_commit_publishes_new_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "pack"
            output.mkdir()
            (output / "01.png").write_bytes(b"old")
            transaction = begin_output_transaction(output, overwrite=True)
            (output / "01.png").write_bytes(b"new")
            transaction.commit()
            self.assertEqual((output / "01.png").read_bytes(), b"new")
            self.assertFalse(transaction.journal.exists())
            self.assertFalse(transaction.backup and transaction.backup.exists())

    def test_output_transaction_recovers_interrupted_commit_as_committed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "pack"
            output.mkdir()
            (output / "01.png").write_bytes(b"old")
            interrupted = begin_output_transaction(output, overwrite=True)
            (output / "01.png").write_bytes(b"new")
            interrupted._write_journal("committing")
            atexit.unregister(interrupted.rollback)
            interrupted.started = False
            journal = json.loads(interrupted.journal.read_text(encoding="utf-8"))
            journal["pid"] = -1
            interrupted.journal.write_text(json.dumps(journal), encoding="utf-8")
            recovered = begin_output_transaction(output, overwrite=True)
            recovered.rollback()
            self.assertEqual((output / "01.png").read_bytes(), b"new")

    def test_attempt_ledger_blocks_ambiguous_replay_and_resumes_xai_request(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ledger = root / "attempt-ledger.json"
            result = root / "result.json"
            route_value = {
                "version": 1,
                "task_sha256": "task",
                "config_sha256": "config",
                "attempts": [
                    {"attempt": 1, "id": "xai-direct", "driver": "command", "execution": {}}
                ],
            }
            claim_attempt(ledger, route_value, 1, result)
            with self.assertRaisesRegex(ContractError, "already uncertain"):
                claim_attempt(ledger, route_value, 1, result)
            atomic_write_json(
                progress_path_for(ledger, 1),
                {"version": 1, "provider": "xai-direct", "status": "submitted", "request_id": "req-1"},
            )
            resumed = claim_attempt(ledger, route_value, 1, result, resume=True)
            self.assertEqual(resumed["resume_request_id"], "req-1")
            result.write_text('{"status":"succeeded"}', encoding="utf-8")
            finish_attempt(ledger, route_value, 1, "succeeded", result_path=result)
            self.assertTrue(claim_attempt(ledger, route_value, 1, result)["idempotent"])
            history = json.loads(ledger.read_text(encoding="utf-8"))["attempts"][0]["events"]
            self.assertEqual(
                [event["status"] for event in history],
                ["planned", "running", "uncertain", "running", "succeeded"],
            )

    def test_superseded_attempt_ledger_is_archived_without_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ledger = root / "attempt-ledger.json"
            route_value = {
                "version": 1,
                "task_sha256": "task",
                "config_sha256": "config",
                "attempts": [{"attempt": 1, "id": "xai-direct", "driver": "command"}],
            }
            claim_attempt(ledger, route_value, 1, root / "result.json")
            progress = progress_path_for(ledger, 1)
            atomic_write_json(
                progress,
                {"version": 1, "provider": "xai-direct", "status": "submitted", "request_id": "req-1"},
            )
            with ledger_lock(ledger):
                archived = archive_ledger(ledger)
            self.assertEqual(len(archived), 2)
            self.assertTrue(all(path.is_file() for path in archived))
            self.assertFalse(ledger.exists())
            self.assertFalse(progress.exists())

    def test_artifact_manifest_detects_current_artifact_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "artifact-manifest.json"
            source = root / "source.png"
            result = root / "result.json"
            source.write_bytes(b"source")
            source_id = record_artifact(
                manifest, source, kind="static-sheet", stage="approved", workspace=root
            )
            result.write_bytes(b"result")
            record_artifact(
                manifest, result, kind="provider-result", stage="executed", dependencies=[source_id]
            )
            self.assertTrue(verify_manifest(manifest)["valid"])
            result.write_bytes(b"result-v2")
            record_artifact(
                manifest, result, kind="provider-result", stage="executed", dependencies=[source_id]
            )
            self.assertTrue(verify_manifest(manifest)["valid"])
            result.write_bytes(b"result")
            record_artifact(
                manifest, result, kind="provider-result", stage="executed", dependencies=[source_id]
            )
            self.assertTrue(verify_manifest(manifest)["valid"])
            result.write_bytes(b"tampered")
            with self.assertRaisesRegex(ContractError, "hash mismatch"):
                verify_manifest(manifest)

    def test_accepted_grok_attempt_is_moved_to_canonical_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            attempt = root / "grok-build-local-attempt-1.mp4"
            target = root / "grok-build-local.mp4"
            attempt.write_bytes(b"video")
            promoted = promote_accepted_video(attempt, target, 1024)
            self.assertEqual(promoted, target.resolve())
            self.assertFalse(attempt.exists())
            self.assertEqual(target.read_bytes(), b"video")

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

    def test_grok_session_video_recovery_uses_the_output_directory_scope(self) -> None:
        from urllib.parse import quote

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "grok-home"
            output = root / "works" / "奶油兔" / "raw-video"
            video = home / "sessions" / quote(str(output.resolve()), safe="") / "session-id" / "videos" / "1.mp4"
            video.parent.mkdir(parents=True)
            video.write_bytes(b"generated")
            self.assertEqual(grok_session_videos(home, output), [video.resolve()])

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

    def test_grok_instruction_makes_green_screen_a_hard_output_contract(self) -> None:
        instruction = build_instruction(
            {"input_image": "/tmp/approved.png"},
            {"grid_video_prompt": "small independent loops"},
            Path("/tmp/grok.mp4"),
            6,
            "480p",
            "#00FF00",
        )
        self.assertIn("duration=6s", instruction)
        self.assertIn("exactly one flat RGB color: #00FF00", instruction)
        self.assertIn("Never draw a checkerboard", instruction)
        self.assertIn("exactly one image_to_video generation", instruction)
        self.assertIn("complete the action by 1.8s", instruction)
        self.assertIn("return to the start pose by 2.6s", instruction)
        self.assertIn("then hold through 3s", instruction)
        self.assertIn("keep holding the start pose through 6s", instruction)
        self.assertIn("do not retry", instruction)

    def test_grok_instruction_compacts_approved_tile_plan_under_cli_budget(self) -> None:
        prompt = {
            "detected_layout": {"columns": 3, "rows": 3, "count": 9},
            "tile_plan": [
                {"id": f"{index:02d}", "motion": f"small in-place action {index}"}
                for index in range(1, 10)
            ],
            "grid_video_prompt": "verbose fallback prompt " * 500,
        }
        compact = compact_motion_prompt(prompt)
        self.assertIn("3x3 grid, 9 cells", compact)
        self.assertIn("01:small in-place action 1", compact)
        instruction = build_instruction(
            {"input_image": "/tmp/approved.png"},
            prompt,
            Path("/tmp/grok.mp4"),
            6,
            "480p",
            "#00FF00",
        )
        self.assertLessEqual(len(instruction.encode("utf-8")), 3800)

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
            ), patch(
                "execute_video_route.probe_video_alpha",
                return_value={"valid": True, "has_meaningful_alpha": True, "classification": "real-alpha"},
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
            ("min_guard_fraction", 0.25, "min_guard_fraction"),
            ("max_foreground_bbox_fraction", 0.95, "max_foreground_bbox_fraction"),
        ):
            invalid = dict(task)
            invalid[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ContractError, message):
                validate_video_task(invalid, require_execution_fields=True)

        invalid = {**task, "min_guard_fraction": 0.10, "max_foreground_bbox_fraction": 0.85}
        with self.assertRaisesRegex(ContractError, "two-sided guard"):
            validate_video_task(invalid, require_execution_fields=True)

    def test_video_task_duration_map_must_include_selected_provider(self) -> None:
        task = {
            "version": 1,
            "operation": "image-to-video",
            "provider": "grok-build-local",
            "input_image": "/tmp/sheet.png",
            "layout_file": "/tmp/layout.json",
            "prompt_file": "/tmp/prompts.json",
            "approval_file": "/tmp/job-state.json",
            "output_directory": "/tmp/raw",
            "duration_seconds": 6,
            "provider_duration_seconds": {"xai-direct": 3},
        }
        with self.assertRaisesRegex(ContractError, "missing selected provider"):
            validate_video_task(task, require_execution_fields=True)
        task["provider_duration_seconds"] = {"grok-build-local": 6, "xai-direct": 3}
        self.assertEqual(validate_video_task(task, require_execution_fields=True), task)

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

    def test_route_hash_changes_when_prompt_content_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prompt = Path(temporary) / "prompts.json"
            prompt.write_text('{"grid_video_prompt":"first"}', encoding="utf-8")
            config = base_config()
            report = {
                "version": 1,
                "config_sha256": object_sha256(config),
                "providers": [],
                "local_processing": {},
            }
            task = {
                "version": 1,
                "operation": "image-to-video",
                "prompt_file": str(prompt),
                "allow_fallback": False,
            }
            first = route(config, report, task)["dependency_sha256"]["prompt_file"]
            prompt.write_text('{"grid_video_prompt":"second"}', encoding="utf-8")
            second = route(config, report, task)["dependency_sha256"]["prompt_file"]
            self.assertNotEqual(first, second)

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

    def test_output_must_be_disjoint_from_protected_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.mkdir()
            (source / "01.png").write_bytes(b"source")
            for output in (source, source / "generated", Path(temporary)):
                with self.subTest(output=output), self.assertRaisesRegex(ValueError, "disjoint|overlaps"):
                    validate_output_boundaries(output, [source])

    def test_generic_motion_requires_explicit_opt_in(self) -> None:
        with self.assertRaisesRegex(ValueError, "tile-plan"):
            load_tile_plan(None, 9)
        self.assertEqual(len(load_tile_plan(None, 9, allow_generic=True)), 9)

    def test_numeric_keypose_sorting(self) -> None:
        ordered = sorted((Path("10"), Path("2"), Path("01")), key=natural_key)
        self.assertEqual([item.name for item in ordered], ["01", "2", "10"])


if __name__ == "__main__":
    unittest.main()
