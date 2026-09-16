"""Shared, read-only Dockerfile instruction and stage identity model.

This is not a build evaluator. Dynamic FROM expressions stay dynamic; no ARG is
resolved from the scanner's environment. Consumers share stage classification so
an internal build stage cannot become either an image dependency or a tag alert.
"""
import re
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class Instruction:
    line: int
    keyword: str
    arguments: str


@dataclass(frozen=True)
class FromReference:
    line: int
    image: str
    alias: str
    kind: str  # external | stage | scratch | dynamic


_HEREDOC = re.compile(r"<<-?\s*(?:'([^']+)'|\"([^\"]+)\"|([A-Za-z_][A-Za-z0-9_]*))")
_FROM = re.compile(
    r"^(?:--platform(?:=|\s+)\S+\s+)?(\S+)(?:\s+AS\s+([A-Za-z0-9_.-]+))?\s*$",
    re.IGNORECASE,
)


def heredoc_delimiters(arguments: str) -> list[str]:
    """Recognize shell heredoc operators, not quoted strings or here-strings."""
    result: list[str] = []
    quote = ""
    offset = 0
    while offset < len(arguments):
        char = arguments[offset]
        if char == "\\" and quote != "'":
            offset += 2
            continue
        if quote:
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif arguments.startswith("<<<", offset):
            offset += 3
            continue
        elif arguments.startswith("<<", offset):
            match = _HEREDOC.match(arguments, offset)
            if match:
                result.append(next(group for group in match.groups() if group is not None))
                offset = match.end()
                continue
        offset += 1
    return result


def instructions(content: str) -> Iterator[Instruction]:
    """Yield logical instructions with original first-line coordinates.

    Comments, continuations and heredoc bodies are not separate instructions.
    Unsupported/malformed FROM syntax is left unclassified, not guessed.
    """
    escape = "\\"
    pending = ""
    first = 0
    heredocs: list[str] = []
    started = False
    for line, raw in enumerate(content.splitlines(), 1):
        stripped = raw.strip()
        if heredocs:
            if stripped == heredocs[0]:
                heredocs.pop(0)
            continue
        if not started and not pending and stripped.startswith("#"):
            directive = re.fullmatch(r"#\s*escape\s*=\s*([`\\])\s*", stripped, re.IGNORECASE)
            if directive:
                escape = directive.group(1)
        if not stripped or stripped.startswith("#"):
            continue
        started = True
        if not pending:
            first = line
        continued = stripped.endswith(escape)
        pending += (stripped[:-1] + " ") if continued else stripped
        if continued:
            continue
        match = re.match(r"^([A-Za-z]+)\s+(.*)$", pending, re.DOTALL)
        pending = ""
        if not match:
            continue
        keyword, arguments = match.group(1).upper(), match.group(2)
        if keyword in {"RUN", "COPY", "ADD"}:
            heredocs = heredoc_delimiters(arguments)
        yield Instruction(first, keyword, arguments)


def from_references(content: str) -> list[FromReference]:
    """Classify FROM against previously declared aliases, scoped to one file."""
    aliases: set[str] = set()
    result: list[FromReference] = []
    for instruction in instructions(content):
        if instruction.keyword != "FROM":
            continue
        match = _FROM.fullmatch(instruction.arguments)
        if not match:
            continue
        image, alias = match.group(1), match.group(2) or ""
        folded = image.casefold()
        kind = ("dynamic" if "$" in image else "stage" if folded in aliases
                else "scratch" if folded == "scratch" else "external")
        result.append(FromReference(instruction.line, image, alias, kind))
        if alias:
            aliases.add(alias.casefold())
    return result


def image_version(image: str) -> tuple[str, str]:
    """Split registry-port-safe image names; a digest takes precedence over a tag."""
    name, separator, digest = image.partition("@")
    last = name.rsplit("/", 1)[-1]
    tag = "latest"
    if ":" in last:
        name, tag = name.rsplit(":", 1)
    return name, digest if separator else tag


def unpinned_image(image: str) -> bool:
    """True for external images using latest/implicit tags, not digest references."""
    return "@" not in image and image_version(image)[1] == "latest"
