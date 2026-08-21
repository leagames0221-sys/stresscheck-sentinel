"""Application layer: the one-way path a submission takes, and an HTTP face for it.

`service` holds the flow — score, classify, draft, review, release — and knows
nothing about HTTP. `server` is a thin stdlib request handler over it, bound to
loopback. Everything a person ever sees leaves through `service`, and everything
that leaves `service` has been through the gate chain.
"""
