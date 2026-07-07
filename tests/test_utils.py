from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from a_share_claw.utils import parse_datetime, parse_interval_seconds, safe_join
from a_share_claw.config import AppConfig


class UtilsTest(unittest.TestCase):
    def test_parse_interval_seconds(self) -> None:
        self.assertEqual(parse_interval_seconds("daily"), 86400)
        self.assertEqual(parse_interval_seconds("every 2 hours"), 7200)
        self.assertEqual(parse_interval_seconds("每 3 天"), 259200)

    def test_parse_datetime_assumes_configured_timezone(self) -> None:
        dt = parse_datetime("2026-07-02 09:30", "Asia/Shanghai")
        self.assertEqual(dt.isoformat(), "2026-07-02T01:30:00+00:00")

    def test_safe_join_rejects_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(safe_join(root, "a/b.txt"), (root / "a" / "b.txt").resolve())
            with self.assertRaises(ValueError):
                safe_join(root, "../outside.txt")

    def test_config_loads_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text("ASCLAW_OPENAI_MODEL=test-model\n", encoding="utf-8")
            import os

            old = os.environ.pop("ASCLAW_OPENAI_MODEL", None)
            try:
                self.assertEqual(AppConfig.from_env(root).openai_model, "test-model")
            finally:
                os.environ.pop("ASCLAW_OPENAI_MODEL", None)
                if old is not None:
                    os.environ["ASCLAW_OPENAI_MODEL"] = old


if __name__ == "__main__":
    unittest.main()
