"""A local stand-in Redis server for the rate limiter's first-use tests.

It listens on a socket the test binds (loopback or a local socket path), reads
the client's stream into a buffer, and answers each complete command: the
handshake (``HELLO``) with a protocol map, the limiter's script (``EVAL``) with
a window admitting the request, and anything else with ``+OK``. Commands are
parsed from complete RESP arrays of bulk strings, so a command split across
reads is answered once it is whole.
"""

import socket
import threading

# Replies by command name: the handshake, and the limiter's script admitting
# the request as the first hit of the window.
PEER_REPLIES = {
    b"HELLO": b"%1\r\n+proto\r\n:3\r\n",
    b"EVAL": b"*4\r\n:1\r\n:1\r\n$1\r\n0\r\n$1\r\n0\r\n",
}
DEFAULT_REPLY = b"+OK\r\n"
_CRLF = b"\r\n"


class RespProtocolError(ValueError):
    """The stream is not a sequence of RESP arrays of bulk strings."""


def _read_line(buffer: bytes, start: int) -> tuple[bytes, int] | None:
    """Return the line at ``start`` without its CRLF and the next offset."""
    end = buffer.find(_CRLF, start)
    if end < 0:
        return None
    return buffer[start:end], end + len(_CRLF)


def _read_count(buffer: bytes, start: int, marker: bytes) -> tuple[int, int] | None:
    """Read a ``<marker><digits>`` header line; ``None`` until it is complete."""
    line = _read_line(buffer, start)
    if line is None:
        return None
    text, offset = line
    if not text.startswith(marker) or not text[1:].isdigit():
        raise RespProtocolError("Expected a RESP array or bulk string header")
    return int(text[1:]), offset


def _parse_one(buffer: bytes, start: int) -> tuple[list[bytes], int] | None:
    """Parse one command from ``start``; ``None`` until it is complete."""
    header = _read_count(buffer, start, b"*")
    if header is None:
        return None
    count, offset = header
    arguments: list[bytes] = []
    for _index in range(count):
        length_header = _read_count(buffer, offset, b"$")
        if length_header is None:
            return None
        length, offset = length_header
        end = offset + length
        if len(buffer) < end + len(_CRLF):
            return None
        if buffer[end : end + len(_CRLF)] != _CRLF:
            raise RespProtocolError("Bulk string is not terminated by CRLF")
        arguments.append(buffer[offset:end])
        offset = end + len(_CRLF)
    return arguments, offset


def parse_commands(buffer: bytes) -> tuple[list[list[bytes]], bytes]:
    """Split ``buffer`` into complete commands and the incomplete remainder.

    Args:
        buffer: Bytes received so far and not yet answered.

    Returns:
        The complete commands, each a list of its arguments (the first is the
        command name), and the bytes of a command still being received.

    Raises:
        RespProtocolError: When the bytes are not RESP arrays of bulk strings.
    """
    commands: list[list[bytes]] = []
    offset = 0
    while offset < len(buffer):
        parsed = _parse_one(buffer, offset)
        if parsed is None:
            break
        command, offset = parsed
        commands.append(command)
    return commands, buffer[offset:]


def reply_to(command: list[bytes]) -> bytes:
    """Return the stand-in server's reply to one complete command."""
    name = command[0].upper() if command else b""
    return PEER_REPLIES.get(name, DEFAULT_REPLY)


def _answer(connection: socket.socket) -> None:
    """Answer each complete command the client sends, until it closes."""
    connection.settimeout(2.0)
    pending = b""
    with connection:
        while True:
            try:
                received = connection.recv(65536)
            except OSError:
                return
            if not received:
                return
            try:
                commands, pending = parse_commands(pending + received)
            except RespProtocolError:
                return
            if not commands:
                continue
            try:
                connection.sendall(b"".join(reply_to(command) for command in commands))
            except OSError:
                return


def start_peer(listener: socket.socket) -> None:
    """Serve the stand-in server on ``listener`` until it is closed.

    Args:
        listener: A bound socket; each accepted connection is answered on its
            own thread.
    """
    listener.listen()

    def accept() -> None:
        """Hand each connection to its own thread."""
        while True:
            try:
                connection, _address = listener.accept()
            except OSError:
                return
            threading.Thread(target=_answer, args=(connection,), daemon=True).start()

    threading.Thread(target=accept, daemon=True).start()
