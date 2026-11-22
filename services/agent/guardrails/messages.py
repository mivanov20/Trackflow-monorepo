"""What the agent says when a guardrail acts. Fixed texts, written once: a refusal never depends on what the model feels like saying, so it cannot be argued with.

Each one refuses in a sentence or two, never explains WHICH rule was triggered (that would help someone work around it), and always points back to what the agent is for:
tracking a shipment, return policies and SLAs by country, and delivery incidents.
"""

from __future__ import annotations

from .scope import country_name

PURPOSE = "tracking a shipment, return policies and SLAs (United States and Spain), and delivery incidents such as a lost parcel, a failed delivery or a wrong address"
HELP_LINE = f"I'm TrackFlow's customer support assistant, and I can help with {PURPOSE}."

INSTRUCTION_CHANGE = f"I can't change or ignore my instructions, and I won't work without them. {HELP_LINE} What do you need help with?"
PROMPT_EXTRACTION = f"I can't share my internal instructions. {HELP_LINE} What can I help you with?"
PERSONAL_TASK = f"I can't help with that: it has nothing to do with your shipments. {HELP_LINE} Tell me about your order, return or delivery problem and I'll get started."
AUTHORIZATION = (
    "I can only share information about orders linked to your account, and I can't confirm that order for this session. "
    "If it's yours, please sign in with the account that placed it or contact our support team. I'm happy to help with your own shipments, returns or delivery incidents."
)
REDIRECT = f"\n\nBy the way, {HELP_LINE} Is there anything I can do for you on that?"
OUTPUT_WITHHELD = f"I can't share that information. {HELP_LINE} What would you like to know about your order or return?"
OUTPUT_FALLBACK = "I couldn't put together a reliable answer just now. Please try again in a moment, or contact our support team if it's urgent."
OUTPUT_LEAK = f"I can't share my internal instructions. {HELP_LINE} What can I help you with?"


def policy_mix(requested: str, actual: str | None) -> str:
    where = country_name(actual)
    if actual:
        return (
            f"I can't apply {country_name(requested)}'s return policy to this order: return policies depend on the country where the order was shipped, and this one was shipped in {where}. "
            f"The policy of {where} applies. I can explain it if you'd like, including the return window and who pays the return shipping."
        )
    return (
        f"I can't apply {country_name(requested)}'s return policy to an order from another country: the policy always depends on the country where the order was shipped. "
        "Tell me the order number and I'll tell you which policy applies to it and what it says."
    )
