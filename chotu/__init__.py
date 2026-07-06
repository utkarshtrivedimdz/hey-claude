"""chotu — wake-word voice controller for Claude Code (macOS).

Package layout (see docs/ARCHITECTURE.md):
  config     — load/validate config.toml
  ports      — AXPort / KeysPort / SystemPort protocols (dependency inversion)
  commands   — pure: trailing-token match, disambiguation, strip (Option A)
  state      — pure: FR-6 state machine
  telemetry  — FR-7 JSONL logging + redaction
  bootstrap  — open VS Code / workspace / focus-safety gate
  ax/keys/system — macOS I/O (pyobjc + osascript); the only pyobjc importers
  log        — human-readable debug stream (stdlib logging → stderr + rolling file)
  wake       — openWakeWord listener
  __main__   — wires the CFRunLoop runtime
"""

import logging as _logging

# Library convention: a NullHandler on the package root so records don't hit the
# last-resort handler when nothing is configured (e.g. under pytest). chotu/log.py's
# configure() attaches the real stderr + file handlers at daemon startup.
_logging.getLogger("chotu").addHandler(_logging.NullHandler())

__version__ = "0.1.0"
