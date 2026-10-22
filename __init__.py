"""Evergreen — a career workspace and LaTeX CV builder for Hermes.

The Hermes plugin loader imports this file as the plugin module, so
``register(ctx)`` must be reachable here.
"""

from __future__ import annotations

from .evergreen import (  # noqa: F401
    TOOLS,
    career_build,
    career_cli,
    career_command,
    career_ingest,
    career_init,
    career_match,
    career_profile,
    career_templates,
    register,
    setup_cli,
)

__all__ = ["register", "TOOLS"]
