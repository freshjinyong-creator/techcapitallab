#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stage 2 Weekday Automation Orchestrator for TechCapitalLab Quant Pipeline.

Coordinates the daily KRX market data update, R2 chart export/upload,
quant advisor snapshot generation, and snapshot upload under an exclusive
file lock (.cache/daily_pipeline.lock) with market-sync early-exit,
real-time timestamped logging, and plain-text Telegram failure alerts.
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import json
import os
import shutil
import stat
import subprocess
import sys
from datetime import datetime
from typing import TextIO

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from backfill_kis_daily_parquet import (  # noqa: E402
    OUTPUT_PARQUET_DIR,
    get_latest_krx_trading_date,
    get_parquet_max_trade_date,
)
from kis_client import KISClient  # noqa: E402
from quant_advisor_engine import load_telegram_credentials  # noqa: E402

LOCK_PATH = os.path.join(BASE_DIR, ".cache", "daily_pipeline.lock")
LOG_DIR = os.path.join(BASE_DIR, "logs")
LOG_PATH = os.path.join(LOG_DIR, "daily_pipeline.log")
SNAPSHOT_PATH = os.path.join(BASE_DIR, "public", "data", "quant_advisor_snapshot.json")
R2_CHART_SRC = "/home/freshjinyong/quant_data/r2_chart_export"
R2_MANIFEST_PATH = os.path.join(BASE_DIR, ".cache", "r2_upload_manifest.json")


class PipelineLogger:
    """Timestamped logger writing to stdout and optionally logs/daily_pipeline.log."""

    def __init__(self, log_path: str) -> None:
        self._file: TextIO | None = None
        if not self._is_stdout_redirected_to_file():
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            self._file = open(log_path, "a", encoding="utf-8")

    @staticmethod
    def _is_stdout_redirected_to_file() -> bool:
        """Return True when stdout is already redirected to a regular file (e.g. cron)."""
        try:
            st = os.fstat(sys.stdout.fileno())
            return stat.S_ISREG(st.st_mode)
        except Exception:
            return False

    def log(self, message: str) -> None:
        """Write a timestamped line to stdout and the log file (if active)."""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {message}"
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
        if self._file is not None:
            self._file.write(line + "\n")
            self._file.flush()

    def close(self) -> None:
        """Close the underlying log file if one was opened."""
        if self._file is not None:
            try:
                self._file.close()
            finally:
                self._file = None


def resolve_node_binary() -> str:
    """Locate the Node.js binary across interactive and cron environments."""
    candidates = [
        shutil.which("node"),
        os.path.expanduser("~/.local/bin/node"),
        "/usr/local/bin/node",
        "/usr/bin/node",
    ]
    for path in candidates:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    raise FileNotFoundError("Node.js binary not found in PATH or standard locations.")


def acquire_lock(lock_path: str) -> TextIO:
    """Acquire an exclusive non-blocking flock on lock_path."""
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    lock_file = open(lock_path, "a+", encoding="utf-8")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        lock_file.close()
        if exc.errno in (errno.EAGAIN, errno.EACCES):
            raise BlockingIOError(f"Lock already held: {lock_path}") from exc
        raise
    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(f"{os.getpid()}\n")
    lock_file.flush()
    return lock_file


def read_snapshot_market_date(snapshot_path: str) -> str | None:
    """Return macro.lastMarketDate from the advisor snapshot JSON, or None if unavailable."""
    if not os.path.isfile(snapshot_path):
        return None
    try:
        with open(snapshot_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        val = data.get("macro", {}).get("lastMarketDate")
        return str(val).strip() if val else None
    except Exception:
        return None


def verify_snapshot_portfolio_empty(snapshot_path: str) -> None:
    """Verify public/data/quant_advisor_snapshot.json has portfolioEvaluated == []."""
    if not os.path.isfile(snapshot_path):
        raise RuntimeError(f"Snapshot file missing: {snapshot_path}")
    with open(snapshot_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    portfolio_eval = data.get("portfolioEvaluated")
    if not isinstance(portfolio_eval, list) or len(portfolio_eval) != 0:
        raise RuntimeError(
            "Safety check failed: portfolioEvaluated must be [] in "
            f"{snapshot_path} before R2 upload (got {type(portfolio_eval).__name__}, "
            f"len={len(portfolio_eval) if isinstance(portfolio_eval, list) else 'N/A'})."
        )


def send_failure_telegram(step_name: str, error_summary: str, logger: PipelineLogger) -> None:
    """Send a concise plain-text Telegram error alert without exposing secrets."""
    token, chat_id = load_telegram_credentials()
    if not token or not chat_id:
        logger.log("[WARN] Telegram credentials not configured; skipping failure alert.")
        return

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    text = (
        "[TechCapitalLab] Daily Pipeline FAILED\n"
        f"Time: {now_str}\n"
        f"Step: {step_name}\n"
        f"Error: {error_summary[:500]}"
    )
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=10,
        )
        if resp.status_code == 200:
            logger.log("[ALERT] Sent Telegram failure notification.")
        else:
            logger.log(f"[WARN] Telegram failure notification returned HTTP {resp.status_code}.")
    except Exception as exc:
        logger.log(f"[WARN] Telegram failure notification failed ({type(exc).__name__}).")


def run_step(step_name: str, cmd: list[str], logger: PipelineLogger) -> None:
    """Run a subprocess command with real-time timestamped output streaming."""
    logger.log(f"=== START {step_name} ===")
    logger.log(f"Command: {' '.join(cmd)}")
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    proc = subprocess.Popen(
        cmd,
        cwd=BASE_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )
    assert proc.stdout is not None
    for raw_line in proc.stdout:
        logger.log(f"[{step_name}] {raw_line.rstrip()}")

    rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"{step_name} exited with non-zero status {rc}")
    logger.log(f"=== DONE {step_name} (rc=0) ===")


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for the daily pipeline orchestrator."""
    parser = argparse.ArgumentParser(
        description="Stage 2 weekday automation orchestrator for TechCapitalLab."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass up-to-date / holiday early-exit check and force backfill.",
    )
    parser.add_argument(
        "--skip-backfill",
        action="store_true",
        help="Skip Step 1 (backfill_kis_daily_parquet.py --incremental).",
    )
    parser.add_argument(
        "--no-telegram",
        action="store_true",
        help="Disable Telegram notifications (advisor report and failure alerts).",
    )
    parser.add_argument(
        "--skip-charts",
        action="store_true",
        help="Skip Step 2 (export_r2_charts.py) and Step 3 (R2 chart upload).",
    )
    return parser.parse_args()


def main() -> int:
    """Run the Stage 2 daily pipeline under an exclusive lock."""
    args = parse_args()
    logger = PipelineLogger(LOG_PATH)
    lock_file: TextIO | None = None
    current_step = "init"

    try:
        try:
            lock_file = acquire_lock(LOCK_PATH)
        except BlockingIOError:
            logger.log(f"[LOCK] Another daily_pipeline instance is already running ({LOCK_PATH}); exiting.")
            return 1

        logger.log(
            f"[INIT] Pipeline started (force={args.force}, skip_backfill={args.skip_backfill}, "
            f"skip_charts={args.skip_charts}, no_telegram={args.no_telegram})"
        )

        # (b) Check latest KRX trading date vs Parquet and advisor snapshot
        current_step = "market_sync_check"
        parquet_max_dt = get_parquet_max_trade_date(OUTPUT_PARQUET_DIR)
        snapshot_last_dt = read_snapshot_market_date(SNAPSHOT_PATH)
        latest_kospi_dt: str | None = None

        try:
            kis = KISClient(min_interval=0.14)
            latest_kospi_dt = get_latest_krx_trading_date(kis)
        except Exception as exc:
            if not args.force:
                raise RuntimeError(
                    f"Failed to query latest KRX trading date via KIS ({type(exc).__name__}: {exc})"
                ) from exc
            logger.log(
                f"[WARN] Failed to query latest KRX trading date ({type(exc).__name__}); "
                "continuing because --force is set."
            )

        logger.log(
            f"[SYNC] latest_kospi_date={latest_kospi_dt}, "
            f"parquet_max_date={parquet_max_dt}, "
            f"snapshot_last_market_date={snapshot_last_dt}"
        )

        if (
            latest_kospi_dt
            and parquet_max_dt
            and snapshot_last_dt
            and parquet_max_dt >= latest_kospi_dt
            and snapshot_last_dt >= latest_kospi_dt
            and not args.force
        ):
            logger.log(
                f"[SKIP] Parquet ({parquet_max_dt}) and snapshot ({snapshot_last_dt}) "
                f"are already >= latest KOSPI date ({latest_kospi_dt}); exiting 0."
            )
            return 0

        node_bin = resolve_node_binary()

        # Step 1: Incremental Parquet backfill
        current_step = "step1_backfill"
        if args.skip_backfill:
            logger.log("[SKIP] Step 1 (backfill_kis_daily_parquet.py) skipped via --skip-backfill.")
        else:
            step1_cmd = [
                sys.executable,
                os.path.join("scripts", "backfill_kis_daily_parquet.py"),
                "--incremental",
            ]
            if args.force:
                step1_cmd.append("--force")
            run_step("Step 1: backfill_kis_daily_parquet", step1_cmd, logger)

        # Step 2: Export R2 charts
        current_step = "step2_export_charts"
        if args.skip_charts:
            logger.log("[SKIP] Step 2 (export_r2_charts.py) skipped via --skip-charts.")
        else:
            step2_cmd = [sys.executable, os.path.join("scripts", "export_r2_charts.py")]
            run_step("Step 2: export_r2_charts", step2_cmd, logger)

        # Step 3: Upload exported charts to R2
        current_step = "step3_upload_charts"
        if args.skip_charts:
            logger.log("[SKIP] Step 3 (upload_to_r2.mjs charts) skipped via --skip-charts.")
        else:
            step3_cmd = [
                node_bin,
                "upload_to_r2.mjs",
                "--src",
                R2_CHART_SRC,
                "--manifest",
                os.path.join(".cache", "r2_upload_manifest.json"),
            ]
            run_step("Step 3: upload_to_r2_charts", step3_cmd, logger)

        # Step 4: Run Quant Advisor Engine
        current_step = "step4_quant_advisor"
        step4_cmd = [
            sys.executable,
            os.path.join("scripts", "quant_advisor_engine.py"),
            "--source",
            "parquet",
        ]
        if not args.no_telegram:
            step4_cmd.append("--notify-telegram")
        run_step("Step 4: quant_advisor_engine", step4_cmd, logger)

        # Step 5: Verify portfolioEvaluated == [] BEFORE uploading snapshot to R2
        current_step = "step5_verify_and_upload_snapshot"
        verify_snapshot_portfolio_empty(SNAPSHOT_PATH)
        logger.log("[VERIFY] Confirmed portfolioEvaluated == [] in public/data/quant_advisor_snapshot.json.")
        step5_cmd = [
            node_bin,
            "upload_to_r2.mjs",
            "--snapshot",
            os.path.join("public", "data", "quant_advisor_snapshot.json"),
        ]
        run_step("Step 5: upload_to_r2_snapshot", step5_cmd, logger)

        logger.log("[SUCCESS] Daily pipeline completed cleanly.")
        return 0

    except Exception as exc:
        err_msg = f"{type(exc).__name__}: {exc}"
        logger.log(f"[ERROR] Pipeline failed at {current_step}: {err_msg}")
        if not args.no_telegram:
            send_failure_telegram(current_step, err_msg, logger)
        return 1

    finally:
        if lock_file is not None:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            lock_file.close()
        logger.close()


if __name__ == "__main__":
    sys.exit(main())