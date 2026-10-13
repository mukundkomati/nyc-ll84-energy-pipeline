"""Logging: console for everything, plus files for the pipeline and the EUI review log."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

FLAG_LOGGER = "ll84.flags"
_FMT = "%(asctime)s %(levelname)s %(name)s - %(message)s"


def configure(log_dir: Optional[Path] = None, level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if getattr(root, "_ll84_configured", False):
        return
    root.setLevel(level)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter(_FMT))
    root.addHandler(console)

    if log_dir is not None:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            pipeline_fh = RotatingFileHandler(
                log_dir / "pipeline.log", maxBytes=5_000_000, backupCount=3
            )
            pipeline_fh.setFormatter(logging.Formatter(_FMT))
            root.addHandler(pipeline_fh)

            # Every flagged building is also written to its own review log.
            flags_fh = RotatingFileHandler(
                log_dir / "eui_flags.log", maxBytes=5_000_000, backupCount=3
            )
            flags_fh.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
            logging.getLogger(FLAG_LOGGER).addHandler(flags_fh)
        except OSError:
            # Read-only filesystems (e.g. some managed runtimes): console only.
            root.warning("Could not create log files in %s; logging to console only", log_dir)

    for noisy in ("botocore", "boto3", "urllib3", "s3transfer"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    root._ll84_configured = True  # type: ignore[attr-defined]
