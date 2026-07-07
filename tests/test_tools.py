import asyncio
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from a_share_claw.config import AppConfig
from a_share_claw.db import Storage
from a_share_claw.tools_runtime import ToolRuntime


class ToolsTest(unittest.TestCase):
    def test_read_write_file_tool_is_workspace_scoped(self):
        asyncio.run(self._read_write_file_tool_is_workspace_scoped())

    async def _read_write_file_tool_is_workspace_scoped(self):
        with TemporaryDirectory() as raw_dir:
            tmp_path = Path(raw_dir)
            config = AppConfig.from_env(tmp_path)
            storage = Storage(tmp_path / "test.sqlite3")
            storage.init()
            context = storage.get_or_create_context("local", "u", "local", "User")
            runtime = ToolRuntime(config, storage, context)

            await runtime.write_text_file("notes/a.txt", "hello")
            result = await runtime.read_text_file("notes/a.txt")
            self.assertEqual(result, "hello")

            with self.assertRaises(ValueError):
                await runtime.read_text_file("../outside.txt")
            storage.close()

    def test_bash_disabled_by_default(self):
        asyncio.run(self._bash_disabled_by_default())

    async def _bash_disabled_by_default(self):
        with TemporaryDirectory() as raw_dir:
            tmp_path = Path(raw_dir)
            config = AppConfig.from_env(tmp_path)
            storage = Storage(tmp_path / "test.sqlite3")
            storage.init()
            context = storage.get_or_create_context("local", "u", "local", "User")
            result = await ToolRuntime(config, storage, context).run_bash("echo hi")
            self.assertIn("disabled", result)
            storage.close()


if __name__ == "__main__":
    unittest.main()
