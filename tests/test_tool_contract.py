# SPDX-License-Identifier: GPL-3.0-or-later
"""Verify the new ``Tool`` contract: registry, schema generation, validation."""

import unittest

from local_ai_agent_orchestrator import tools as tools_pkg
from local_ai_agent_orchestrator.tools.base import (
    Tool,
    all_tools,
    get,
    parameters_schema,
    param,
    register,
)


class TestToolContract(unittest.TestCase):
    def test_core_tools_registered(self):
        names = {t.name for t in all_tools()}
        for required in [
            "file_read", "file_write", "file_patch", "list_dir",
            "shell_exec", "find_relevant_files",
            "task_todo_set", "task_todo_get",
            "enter_plan_mode", "exit_plan_mode",
            "memory_read", "memory_append", "memory_forget",
            "skill_list", "skill_run", "skill_clear",
        ]:
            self.assertIn(required, names, f"missing tool: {required}")

    def test_openai_schemas_match_registry(self):
        coder_names = set(tools_pkg._coder_tool_names())
        schemas = {s["function"]["name"] for s in tools_pkg.TOOL_SCHEMAS}
        self.assertEqual(coder_names, schemas)

    def test_dispatch_is_callable(self):
        for name, fn in tools_pkg.TOOL_DISPATCH.items():
            self.assertTrue(callable(fn), f"dispatch missing for {name}")

    def test_validate_rejects_missing_required(self):
        sample = Tool(
            name="_t_sample",
            description="sample",
            parameters=parameters_schema({"x": param("string")}, required=["x"]),
            call=lambda **k: "ok",
        )
        register(sample)
        try:
            with self.assertRaises(ValueError):
                sample.validate({})
            self.assertEqual(sample.validate({"x": "hi"}), {"x": "hi"})
        finally:
            from local_ai_agent_orchestrator.tools.base import _REGISTRY
            _REGISTRY.pop("_t_sample", None)

    def test_get_returns_none_for_missing(self):
        self.assertIsNone(get("__definitely_not_a_tool__"))


if __name__ == "__main__":
    unittest.main()
