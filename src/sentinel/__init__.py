"""stresscheck-sentinel.

A deterministic scoring core for Japan's statutory workplace stress check, plus
the governance layer that decides what an LLM is allowed to touch.

Layering (enforced by lint, see pyproject.toml):

    core   -> depends on nothing but the stdlib
    packs  -> may depend on core
    app/cli-> may depend on core and packs
"""

__version__ = "0.1.0"
