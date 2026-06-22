"""Stop フック本体（Mac/Windows 共通）。

応答終了前に「未記録の変更があるのに docs/worklog.md を更新していない」場合だけ、
worklog への追記を一度だけ促す。stop_hook_active で二重発火（無限ループ）を防ぐ。

呼び出し: .claude/settings.json の Stop フックから `uv run python scripts/worklog_check.py`。
標準入力にフックの JSON が渡る。記録が必要なときだけ stdout に {"decision":"block",...} を出す。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

WORKLOG = "docs/worklog.md"


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}

    # フック継続による二度目の停止では何もしない（ループ防止）。
    if data.get("stop_hook_active"):
        return 0

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        res = subprocess.run(
            ["git", "-C", root, "status", "--porcelain"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return 0  # git が無い等は黙って許可
    if res.returncode != 0:
        return 0

    changed = [ln[3:].strip() for ln in res.stdout.splitlines() if ln.strip()]
    if not changed:
        return 0  # 変更なし＝記録不要
    if any(WORKLOG in c for c in changed):
        return 0  # 既に worklog を更新済み

    msg = (
        "未記録の変更があります。意味のある変更・判断なら docs/worklog.md の先頭に"
        "日付つきで1エントリ追記してください（やったこと／決めたこと／次の一手）。"
        "軽微で記録不要なら、そのまま再度終了して構いません。"
    )
    print(json.dumps({"decision": "block", "reason": msg}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
