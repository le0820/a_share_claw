from __future__ import annotations

from pathlib import Path

_src_main = Path(__file__).resolve().parents[1] / "src" / "a_share_claw" / "__main__.py"
_code = compile(_src_main.read_text(encoding="utf-8"), str(_src_main), "exec")
exec(_code, {"__name__": "__main__", "__file__": str(_src_main), "__package__": "a_share_claw"})
