# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements. See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership. The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License. You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied. See the License for the
# specific language governing permissions and limitations
# under the License.

"""The Alt-Svc field, which names other places the same service is reached, RFC 7838."""

import re
from collections.abc import Iterable
from urllib.parse import quote, unquote

import attrs

__all__ = ["DEFAULT_MAX_AGE", "AltService", "format_alt_svc", "parse_alt_svc"]

# Seconds an alternative stays fresh when its field gives no ma, RFC 7838 section 3.1.
DEFAULT_MAX_AGE = 86400

# RFC 9110 section 5.6.2 and 5.6.4.
_TOKEN = r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+"
_QUOTED = r'"(?:[^"\\]|\\.)*"'
_PARAMETER = re.compile(rf"[ \t]*;[ \t]*({_TOKEN})[ \t]*=[ \t]*({_TOKEN}|{_QUOTED})")
_ALTERNATIVE = re.compile(rf"[ \t]*({_TOKEN})[ \t]*=[ \t]*({_QUOTED})((?:{_PARAMETER.pattern})*)[ \t]*(?:,|$)")
_ESCAPE = re.compile(r"\\(.)")
_CLEAR = re.compile(rb"(?:^|,)[ \t]*clear[ \t]*(?:,|$)", re.IGNORECASE)
_PORT = re.compile(r"[0-9]{1,5}")
# The longest ma kept, in seconds; a longer one means as much.
_LONGEST_MAX_AGE = 2**31 - 1
# Characters of a token that an ALPN protocol keeps unencoded, RFC 7838 section 3: all but "%".
_PROTOCOL_SAFE = "!#$&'*+^`|"


@attrs.frozen(kw_only=True)
class AltService:
    """One alternative service, RFC 7838 section 3.

    Attributes:
        protocol: The ALPN protocol it speaks, such as ``h3``.
        host: The host it is reached at; empty for the host of the origin.
        port: The port it is reached at.
        max_age: Seconds it stays fresh after the response that named it.
        persist: Whether it outlives a change of the client's network.
    """

    protocol: str
    host: str = ""
    port: int
    max_age: int = DEFAULT_MAX_AGE
    persist: bool = False


def parse_alt_svc(value: bytes) -> tuple[AltService, ...] | None:
    """Return the alternatives an Alt-Svc field value names, in its order; ``None`` for ``clear``, which withdraws all.

    ``clear`` withdraws all even beside other entries. An alternative whose
    authority names no valid port is left out, and one with an invalid
    ``ma`` keeps the default; parsing stops at the first entry that breaks
    the syntax, keeping those before it.
    """
    if _CLEAR.search(value):
        return None

    text = value.decode("latin-1")
    alternatives: list[AltService] = []
    position = 0
    # Each match is one alternative, protocol-id="host:port" with its parameters, and the comma after it.
    while position < len(text):
        match = _ALTERNATIVE.match(text, position)
        if match is None:
            break
        position = match.end()

        protocol, authority, parameters = match.group(1, 2, 3)
        # The host may be empty, meaning the origin's, or an IPv6 literal in brackets.
        host, colon, port = _unquote(authority).rpartition(":")
        if not colon or not _PORT.fullmatch(port) or not 0 < int(port) < 65536:
            continue
        options = {name.lower(): _unquote(option) for name, option in _PARAMETER.findall(parameters)}
        alternatives.append(
            AltService(
                protocol=unquote(protocol),
                host=host.removeprefix("[").removesuffix("]"),
                port=int(port),
                max_age=_max_age(options.get("ma", "")),
                persist=options.get("persist") == "1",
            )
        )
    return tuple(alternatives)


def format_alt_svc(alternatives: Iterable[AltService]) -> bytes:
    """Return the Alt-Svc field value that names ``alternatives``, leaving out the parameters at their defaults."""
    entries = []
    for alternative in alternatives:
        host = f"[{alternative.host}]" if ":" in alternative.host else alternative.host
        entry = f'{quote(alternative.protocol, safe=_PROTOCOL_SAFE)}="{host}:{alternative.port}"'
        if alternative.max_age != DEFAULT_MAX_AGE:
            entry += f"; ma={alternative.max_age}"
        if alternative.persist:
            entry += "; persist=1"
        entries.append(entry)
    return ", ".join(entries).encode("latin-1")


def _max_age(text: str) -> int:
    if not (text.isascii() and text.isdigit()):
        return DEFAULT_MAX_AGE
    digits = text.lstrip("0") or "0"
    # More digits than the longest has always mean longer, without reading them all.
    return _LONGEST_MAX_AGE if len(digits) > len(str(_LONGEST_MAX_AGE)) else min(int(digits), _LONGEST_MAX_AGE)


def _unquote(text: str) -> str:
    if text.startswith('"'):
        return _ESCAPE.sub(r"\1", text[1:-1])
    return text
