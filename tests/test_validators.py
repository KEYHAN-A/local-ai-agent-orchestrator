# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for validation helpers."""

import tempfile
import unittest
from pathlib import Path

from local_ai_agent_orchestrator.settings import init_settings, reset_settings_for_tests
from local_ai_agent_orchestrator.validators import (
    extract_written_files,
    validate_files,
    validate_reviewer_json,
    validate_cross_file_consistency,
    infer_plan_languages,
    score_plan_languages,
    infer_languages_from_extensions,
    run_optional_validation_commands,
)


class TestValidators(unittest.TestCase):
    def tearDown(self):
        reset_settings_for_tests()

    def test_extract_written_files(self):
        out = extract_written_files("Done\n\nFiles written: a.swift, b.swift")
        self.assertEqual(out, ["a.swift", "b.swift"])

    def test_validate_files_flags_placeholders_and_codable_any(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = root / "EventModels.swift"
            p.write_text(
                "public struct E: Codable {\n"
                " let data: [String: Any]?\n"
                " // for now\n"
                "}\n",
                encoding="utf-8",
            )
            findings = validate_files(root, ["EventModels.swift"])
            classes = {f.issue_class for f in findings}
            self.assertIn("codable_any", classes)
            self.assertIn("placeholder_text", classes)
            self.assertTrue(all(isinstance(f.confidence, float) for f in findings))
            self.assertTrue(all(bool(f.analyzer_kind) for f in findings))

    def test_validate_reviewer_json(self):
        approved, findings, summary = validate_reviewer_json(
            '{"verdict":"REJECTED","findings":[{"severity":"major","file_path":"x.py","issue_class":"bug","message":"oops","fix_hint":"fix"}],"summary":"Needs changes"}'
        )
        self.assertFalse(approved)
        self.assertEqual(len(findings), 1)
        self.assertEqual(summary, "Needs changes")

    def test_validate_reviewer_json_rejected_with_only_minor_is_non_blocking(self):
        approved, findings, summary = validate_reviewer_json(
            '{"verdict":"REJECTED","findings":[{"severity":"minor","file_path":"x.py","issue_class":"style","message":"nit","fix_hint":"optional"}],"summary":"Optional improvements"}'
        )
        self.assertTrue(approved)
        self.assertEqual(len(findings), 1)
        self.assertEqual(summary, "Optional improvements")

    def test_validate_reviewer_json_markdown_fenced_object_is_parsed(self):
        approved, findings, summary = validate_reviewer_json(
            '```json\n{"verdict":"APPROVED","findings":[{"severity":"minor","file_path":"x.py","issue_class":"style","message":"nit","fix_hint":"optional"}],"summary":"Looks good"}\n```'
        )
        self.assertTrue(approved)
        self.assertEqual(len(findings), 1)
        self.assertEqual(summary, "Looks good")

    def test_cross_file_consistency_flags_missing_symbol(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".lao").mkdir(parents=True, exist_ok=True)
            cfg = root / "factory.yaml"
            cfg.write_text(
                "lm_studio_base_url: http://127.0.0.1:1234\nopenai_api_key: lm-studio\n",
                encoding="utf-8",
            )
            init_settings(config_path=cfg, cwd=root)
            (root / "app.py").write_text("def real_func():\n    return 1\n", encoding="utf-8")
            (root / "test_app.py").write_text(
                "from app import MissingClass\n\nclass TestX:\n    pass\n", encoding="utf-8"
            )
            findings = validate_cross_file_consistency(root, {"python"})
            self.assertTrue(any(f.issue_class == "test_symbol_mismatch" for f in findings))

    def test_validation_profile_commands_are_executed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".lao").mkdir(parents=True, exist_ok=True)
            cfg = root / "factory.yaml"
            cfg.write_text(
                "\n".join(
                    [
                        "lm_studio_base_url: http://127.0.0.1:1234",
                        "openai_api_key: lm-studio",
                        "orchestration:",
                        "  validation_profile: default",
                        "  validation_profiles:",
                        "    default:",
                        "      commands:",
                        "        - kind: build",
                        "          command: \"python -c 'import sys; sys.exit(1)'\"",
                    ]
                ),
                encoding="utf-8",
            )
            init_settings(config_path=cfg, cwd=root)
            findings = run_optional_validation_commands(root, set())
            self.assertTrue(any(f.issue_class == "build_command_failed" for f in findings))

    def test_infer_plan_languages_from_plan_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "LAO_PLAN.md").write_text(
                "# Plan\nBuild an iOS SwiftUI app with a Python service.\n",
                encoding="utf-8",
            )
            langs = infer_plan_languages(root)
            self.assertIn("swift", langs)
            self.assertIn("python", langs)

    def test_rich_language_scoring(self):
        text = "Build with Kotlin + Ktor, Rust services with Cargo, and Solidity contracts via Hardhat."
        scores = score_plan_languages(text.lower())
        self.assertGreaterEqual(scores.get("kotlin", 0), 2)
        self.assertGreaterEqual(scores.get("rust", 0), 2)
        self.assertGreaterEqual(scores.get("solidity", 0), 2)

    def test_extension_language_inference_is_broad(self):
        langs = infer_languages_from_extensions(
            [".kt", ".rs", ".sol", ".ex", ".scala", ".zig", ".cs"]
        )
        self.assertIn("kotlin", langs)
        self.assertIn("rust", langs)
        self.assertIn("solidity", langs)
        self.assertIn("elixir", langs)
        self.assertIn("scala", langs)
        self.assertIn("zig", langs)
        self.assertIn("csharp", langs)


if __name__ == "__main__":
    unittest.main()
