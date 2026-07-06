"""Human-readable debug stream — distinct from FR-7 telemetry (structured tuning
data). This is the "watch what the daemon is doing" channel.

Every module logs via a stdlib logger (`logging.getLogger(__name__)`, e.g.
`hey_claude.state`). `configure()` — called once from `__main__` — attaches two sinks to
the `hey-claude` root logger:

  • stderr        → live stream. In a terminal you see it as it happens; under
                    launchd it lands in ~/Library/Logs/hey-claude/daemon.err.log.
  • hey-claude.log     → a rolling file at <log_dir>/hey-claude.log you can `tail -f` or read
                    AFTER a run (survives the process; rotates at ~2 MB × 3).

Verbosity: INFO by default gives the narrative (wake → arm → dictate → sent /
cancel / timeout). `--debug` or HEY_CLAUDE_DEBUG=1 turns on DEBUG (every keystroke, box
change, AX callback, focus-gate poll).

Until configure() runs there are no handlers, so unit tests stay silent and the
pure modules keep logging without any I/O side effect.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path

_ROOT = "hey_claude"
_configured = False


def debug_enabled(flag: bool = False) -> bool:
    """DEBUG verbosity is on if --debug was passed or HEY_CLAUDE_DEBUG/HEY_CLAUDE_WAKE_DEBUG=1."""
    return (
        flag
        or os.environ.get("HEY_CLAUDE_DEBUG") == "1"
        or os.environ.get("HEY_CLAUDE_WAKE_DEBUG") == "1"
    )


def configure(debug: bool = False, log_dir: str | None = None) -> logging.Logger:
    """Attach stderr + rolling-file handlers to the `hey-claude` logger. Idempotent —
    a second call only adjusts the level, so re-invoking is safe."""
    global _configured
    root = logging.getLogger(_ROOT)
    level = logging.DEBUG if debug else logging.INFO
    root.setLevel(level)

    if _configured:
        for h in root.handlers:
            h.setLevel(level)
        return root

    fmt = logging.Formatter(
        "%(asctime)s.%(msecs)03d %(name)-13s %(levelname).1s %(message)s",
        datefmt="%H:%M:%S",
    )

    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    root.addHandler(sh)

    if log_dir:
        try:
            p = Path(log_dir).expanduser()
            p.mkdir(parents=True, exist_ok=True)
            fh = logging.handlers.RotatingFileHandler(
                p / "hey-claude.log", maxBytes=2_000_000, backupCount=3
            )
            fh.setFormatter(fmt)
            root.addHandler(fh)
        except OSError:
            pass  # a broken log dir must never take the daemon down

    root.propagate = False  # our handlers own the tree; don't double-print via root
    _configured = True
    root.debug("logging configured: level=%s dir=%s", logging.getLevelName(level), log_dir)
    return root
