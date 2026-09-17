"""Input capture: keystrokes and mouse behaviour, for process replication.

Stage 4 cannot replay a process it has only seen from the outside, so this
package records what was typed and clicked. That makes it the most sensitive
code in the project, and it is built accordingly:

* it is **off by default** and must be switched on deliberately;
* it offers four fidelity tiers, so "enough to recognise the process" and
  "enough to replay it exactly" are different, explicit choices;
* typed text is redacted by default - which also happens to be what process
  *replication* wants, since the variable slot generalises and one run's
  literal value does not;
* suppression for password fields and denied applications cannot be configured
  away.

See ``docs/PRIVACY.md`` for the threat model and its limits.
"""

from copynion.inputs.events import (
    Fidelity,
    InputBatch,
    InputCounters,
    InputEvent,
    KeyClass,
    classify_key,
)
from copynion.inputs.recorder import InputRecorder, RecorderSettings
from copynion.inputs.secure_input import is_secure_input_active

__all__ = [
    "Fidelity",
    "InputBatch",
    "InputCounters",
    "InputEvent",
    "InputRecorder",
    "KeyClass",
    "RecorderSettings",
    "classify_key",
    "is_secure_input_active",
]
