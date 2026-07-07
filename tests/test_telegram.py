from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from a_share_claw.telegram import TelegramBot


class TelegramBotTest(unittest.IsolatedAsyncioTestCase):
    async def test_get_updates_treats_read_timeout_as_empty_poll(self) -> None:
        bot = TelegramBot("token", storage=object(), agent=object())  # type: ignore[arg-type]

        with patch("a_share_claw.telegram.post_json", new=AsyncMock(side_effect=TimeoutError("The read operation timed out"))):
            updates = await bot.get_updates(0)

        self.assertEqual(updates, [])


if __name__ == "__main__":
    unittest.main()
