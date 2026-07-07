r"""Dialog classification (Phase 1) — pure policy over raw a11y dicts. Zero pyobjc.

`ax.py` walks a Claude session container and produces raw `{role,label,enabled,selected}`
dicts; `classify()` turns them into a `DialogBox` (or `None`). Keeping the risky
discriminator logic here — not in the pyobjc walk — makes it unit-testable.

Two box types Claude renders (DIALOG-STATE-PLAN §1), both using **numbered** action controls
(`^\d+ ` in the label — the reliable "Claude action control" marker; ordinary VS Code buttons
like "Bypass permissions" are not numbered):

  ① Approval — terminal, single-press: numbered AXButtons ('1 Yes' · '2 Yes, allow…' · '3 No'),
     no radios. One press resolves it.
  ② Choice — select-then-submit: AXRadioButtons (AXValue 0→1 = the pick) + a numbered Submit
     AXButton (AXEnabled False→True once a radio is picked).

Discriminator: radios present → choice; else numbered buttons → approval; neither → None
(an answered/absent prompt exposes no numbered controls — loophole #9).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

# A Claude action control is numbered: "1 Yes", "2 Submit answers", "3 No". This is what
# distinguishes Claude's buttons from VS Code's own (unnumbered) buttons in the same tree.
NUMBERED = re.compile(r"^\s*\d+\s")


@dataclass(frozen=True)
class DialogBox:
    type: str                       # "approval" | "choice"
    session: str                    # owning session title (attribution — see §6 scoping)
    options: tuple[str, ...]        # approval: the numbered buttons; choice: the radio labels
    submit: Optional[str] = None    # choice only: the numbered Submit button label
    submit_enabled: bool = False    # choice only: is Submit pressable yet (a radio picked)?
    selected: Optional[str] = None  # choice only: the currently-picked radio label, if any


def _is_numbered(label: Optional[str]) -> bool:
    return bool(label) and NUMBERED.match(label) is not None


def classify(raw: List[dict], session: str = "") -> Optional[DialogBox]:
    """Pure discriminator over one session container's controls → a DialogBox or None.

    `raw` is already scoped to a single session (ax.find_dialog does the per-container walk),
    so no cross-pane merge happens here. Each dict: {role, label, enabled, selected}.
    """
    radios = [c for c in raw if c.get("role") == "AXRadioButton"]
    numbered_buttons = [
        c for c in raw if c.get("role") == "AXButton" and _is_numbered(c.get("label"))
    ]

    if radios:
        # Choice box: the radios are the options; the (numbered) Submit gates readiness.
        submit = numbered_buttons[0] if numbered_buttons else None
        selected = next((r.get("label") for r in radios if r.get("selected")), None)
        return DialogBox(
            type="choice",
            session=session,
            options=tuple(r.get("label") or "" for r in radios),
            submit=(submit.get("label") if submit else None),
            submit_enabled=bool(submit and submit.get("enabled")),
            selected=selected,
        )

    if numbered_buttons:
        # Approval box: numbered buttons, no radios. Single press resolves.
        return DialogBox(
            type="approval",
            session=session,
            options=tuple(b.get("label") or "" for b in numbered_buttons),
        )

    return None
