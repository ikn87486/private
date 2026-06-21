"""APScheduler によるアプリ内スケジューラ。

毎営業日（月〜金）の引け後 16:30 JST に live.run_daily を自動実行する。
サーバ（アプリ）が起動している間だけ動作する。祝日はその日の新規バーが
取得できないため、判定は実質的に何もしない（取引所カレンダー不要）。
"""

from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import calibration, live, outcomes, report, screener
from .stocks import MARKET_BENCHMARK, UNIVERSE

_scheduler: BackgroundScheduler | None = None

# 実行時刻（日本時間）。東証の大引け15:00後、データ反映を見込んで16:30。
RUN_HOUR = 16
RUN_MINUTE = 30
# スクリーナーの予測保存・実績記入は売買判定の後 16:45。
SCREEN_MINUTE = 45
# 週次レポートは金曜の引け後（売買実行の後）17:00。
REPORT_HOUR = 17
# 週次の精度（ウォークフォワード）再計算は金曜 17:30。
ACCURACY_MINUTE = 30
TIMEZONE = "Asia/Tokyo"


def _job() -> None:
    """毎営業日の売買判定。"""
    try:
        result = live.run_daily("live")
        print(f"[scheduler] 自動売買: {result}")
    except Exception as e:  # noqa: BLE001
        print(f"[scheduler] 自動売買に失敗: {e}")


def _report_job() -> None:
    """週次の振り返りレポート生成。"""
    try:
        r = report.generate_report("live")
        print(f"[scheduler] 週次レポート生成: {r['headline']}")
    except Exception as e:  # noqa: BLE001
        print(f"[scheduler] 週次レポート生成に失敗: {e}")


def _screen_job() -> None:
    """毎営業日: 最新データ取得→スクリーニング保存→期限到来分の実績記入。"""
    try:
        from . import collector

        collector.update_latest(list(UNIVERSE.keys()) + list(MARKET_BENCHMARK.values()))
        results = screener.screen()
        screener.save_snapshot(results)
        filled = outcomes.fill_outcomes()
        print(f"[scheduler] スクリーナー: {len(results)}銘柄保存 / 実績記入 {filled}")
    except Exception as e:  # noqa: BLE001
        print(f"[scheduler] スクリーナーに失敗: {e}")


def _accuracy_job() -> None:
    """週次: 較正と精度（ウォークフォワード/実運用）を再計算。"""
    try:
        calibration.build_calibration()
        calibration.walk_forward_accuracy()
        calibration.live_accuracy()
        print("[scheduler] 精度テーブルを再計算しました")
    except Exception as e:  # noqa: BLE001
        print(f"[scheduler] 精度再計算に失敗: {e}")


def start() -> str:
    """スケジューラを起動し、次回実行時刻の説明を返す。"""
    global _scheduler
    if _scheduler is not None:
        return next_run_text()

    _scheduler = BackgroundScheduler(timezone=TIMEZONE)
    _scheduler.add_job(
        _job,
        CronTrigger(day_of_week="mon-fri", hour=RUN_HOUR, minute=RUN_MINUTE,
                    timezone=TIMEZONE),
        id="daily_trade",
        replace_existing=True,
    )
    _scheduler.add_job(
        _report_job,
        CronTrigger(day_of_week="fri", hour=REPORT_HOUR, minute=0, timezone=TIMEZONE),
        id="weekly_report",
        replace_existing=True,
    )
    _scheduler.add_job(
        _screen_job,
        CronTrigger(day_of_week="mon-fri", hour=RUN_HOUR, minute=SCREEN_MINUTE,
                    timezone=TIMEZONE),
        id="daily_screen",
        replace_existing=True,
    )
    _scheduler.add_job(
        _accuracy_job,
        CronTrigger(day_of_week="fri", hour=REPORT_HOUR, minute=ACCURACY_MINUTE,
                    timezone=TIMEZONE),
        id="weekly_accuracy",
        replace_existing=True,
    )
    _scheduler.start()
    return next_run_text()


def _job_next(job_id: str) -> str:
    """指定ジョブの次回実行時刻テキスト。"""
    if _scheduler is None:
        return "停止中"
    job = _scheduler.get_job(job_id)
    if job and job.next_run_time:
        return job.next_run_time.strftime("%Y-%m-%d %H:%M %Z")
    return "未設定"


def next_run_text() -> str:
    """日次（自動売買）の次回実行時刻。"""
    return _job_next("daily_trade")


def next_report_text() -> str:
    """週次（振り返りレポート）の次回実行時刻。"""
    return _job_next("weekly_report")


def shutdown() -> None:
    """スケジューラを停止する。"""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
