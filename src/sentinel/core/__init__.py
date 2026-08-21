"""Domain-independent machinery: gates, audit log, HITL, LLM providers.

Nothing in here knows what a stress check is. That is deliberate — the gate,
audit and review machinery is the reusable part, and keeping it ignorant of the
questionnaire is what proves it.
"""
