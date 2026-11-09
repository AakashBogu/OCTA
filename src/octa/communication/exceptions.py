"""Exception hierarchy for OCTA communication protocol and transport."""

from __future__ import annotations


class CommunicationError(Exception):
    """Base exception for all communication-related errors."""


class ProtocolError(CommunicationError):
    """Base exception for protocol-level framing, encoding, and schema errors."""


class InvalidMessageError(ProtocolError):
    """Raised when a message is structurally malformed, non-JSON, or missing required fields."""


class UnsupportedProtocolVersionError(ProtocolError):
    """Raised when an incoming message specifies an incompatible protocol version."""


class MessageTooLargeError(ProtocolError):
    """Raised when an incoming or outgoing message exceeds the maximum allowed payload size."""


class PayloadValidationError(ProtocolError):
    """Raised when the message payload fails domain validation or type checking."""


class AuthenticationError(CommunicationError):
    """Raised when handshake authentication fails or credentials are invalid.

    Note: Exception messages must never expose raw tokens or sensitive credentials.
    """


class ConnectionTimeoutError(CommunicationError):
    """Raised when a connection, handshake, or heartbeat operation times out."""
