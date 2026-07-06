"""chotu — wake-word voice controller for Claude Code (macOS).

Package layout (see docs/ARCHITECTURE.md):
  config     — load/validate config.toml
  ports      — AXPort / KeysPort / SystemPort protocols (dependency inversion)
  commands   — pure: trailing-token match, disambiguation, strip (Option A)
  state      — pure: FR-6 state machine
  telemetry  — FR-7 JSONL logging + redaction
  bootstrap  — open VS Code / workspace / focus-safety gate
  ax/keys/system — macOS I/O (pyobjc + osascript); the only pyobjc importers
  wake       — openWakeWord listener
  __main__   — wires the CFRunLoop runtime
"""

__version__ = "0.1.0"
