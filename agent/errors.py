"""Exceptions that map onto tool statuses.

Tools raise these instead of returning ad-hoc strings, so the registry can turn
each one into the right ToolStatus without guessing.
"""

from __future__ import annotations


class AgentError(Exception):
    """Base class for agent failures."""


class ToolUnavailable(AgentError):
    """A backing service is unreachable, unauthenticated or not configured.

    Deliberately distinct from a plain error: it means the agent collected no
    evidence at all and has to say so rather than fill the gap itself.
    """


class PermissionDenied(AgentError):
    """The safety policy refused the call before it reached the service."""


class ConfirmationRequired(AgentError):
    """A state-changing operation was requested without explicit confirmation."""


class InvalidToolRequest(AgentError):
    """Arguments did not match the tool input schema."""
