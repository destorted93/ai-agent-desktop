"""App-level services (pure-ish helpers).

This package exists to keep src/app.py small while preserving the modular monolith.

Rules (v1):
- No GUI imports.
- Keep functions side-effect-light and easy to test.
"""
