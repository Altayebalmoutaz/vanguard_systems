"""Voice payer verification escalation when Stedi 271 is incomplete or ambiguous."""

from app.eligibility.voice.gate import canonical_voice_escalation_eligible
from app.eligibility.voice.queue import maybe_auto_queue_voice_verification, queue_voice_verification

__all__ = [
    "canonical_voice_escalation_eligible",
    "maybe_auto_queue_voice_verification",
    "queue_voice_verification",
]
