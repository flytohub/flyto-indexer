"""
Lightweight token-aware text processing for scanners.

Not a full parser — just enough to:
1. Strip comments and string literals from source code (preserving line numbers)
2. Track brace nesting depth with awareness of strings/comments
3. Extract clean blocks between matched braces
"""

import re


def strip_comments_and_strings(source: str, language: str) -> str:
    """Remove comments and string contents, replacing with spaces to preserve line numbers.

    For Go:   // line comments, /* block comments */, "strings", `raw strings`, 'rune'
    For TS:   // line comments, /* block comments */, "strings", 'strings', `template literals`

    Replaces content characters with spaces but keeps newlines intact so
    line counting remains correct.
    """
    result = []
    i = 0
    length = len(source)

    while i < length:
        c = source[i]

        # Line comment: //
        if c == '/' and i + 1 < length and source[i + 1] == '/':
            result.append(' ')
            result.append(' ')
            i += 2
            while i < length and source[i] != '\n':
                result.append(' ')
                i += 1
            continue

        # Block comment: /* ... */
        if c == '/' and i + 1 < length and source[i + 1] == '*':
            result.append(' ')
            result.append(' ')
            i += 2
            while i < length:
                if source[i] == '\n':
                    result.append('\n')
                elif source[i] == '*' and i + 1 < length and source[i + 1] == '/':
                    result.append(' ')
                    result.append(' ')
                    i += 2
                    break
                else:
                    result.append(' ')
                i += 1
            continue

        # Go raw string: `...`
        if language == "go" and c == '`':
            result.append(' ')
            i += 1
            while i < length and source[i] != '`':
                if source[i] == '\n':
                    result.append('\n')
                else:
                    result.append(' ')
                i += 1
            if i < length:
                result.append(' ')  # closing `
                i += 1
            continue

        # TS template literal: `...` — handle ${} nesting
        if language == "ts" and c == '`':
            result.append(' ')
            i += 1
            depth = 0
            while i < length:
                tc = source[i]
                if tc == '\\' and i + 1 < length:
                    result.append(' ')
                    result.append(' ')
                    i += 2
                    continue
                if tc == '`' and depth == 0:
                    result.append(' ')
                    i += 1
                    break
                if tc == '$' and i + 1 < length and source[i + 1] == '{':
                    # Keep ${ as space but track depth
                    result.append(' ')
                    result.append(' ')
                    i += 2
                    depth += 1
                    continue
                if tc == '{' and depth > 0:
                    result.append(' ')
                    depth += 1
                    i += 1
                    continue
                if tc == '}' and depth > 0:
                    depth -= 1
                    result.append(' ')
                    i += 1
                    if depth == 0:
                        # Back in template literal
                        pass
                    continue
                if tc == '\n':
                    result.append('\n')
                else:
                    if depth > 0:
                        # Inside ${}, keep content (it's code)
                        result.append(tc)
                    else:
                        result.append(' ')
                i += 1
            continue

        # Double-quoted string
        if c == '"':
            result.append(' ')
            i += 1
            while i < length:
                sc = source[i]
                if sc == '\\' and i + 1 < length:
                    result.append(' ')
                    result.append(' ')
                    i += 2
                    continue
                if sc == '"':
                    result.append(' ')
                    i += 1
                    break
                if sc == '\n':
                    result.append('\n')
                else:
                    result.append(' ')
                i += 1
            continue

        # Single-quoted string
        if c == "'":
            result.append(' ')
            i += 1
            while i < length:
                sc = source[i]
                if sc == '\\' and i + 1 < length:
                    result.append(' ')
                    result.append(' ')
                    i += 2
                    continue
                if sc == "'":
                    result.append(' ')
                    i += 1
                    break
                if sc == '\n':
                    result.append('\n')
                else:
                    result.append(' ')
                i += 1
            continue

        result.append(c)
        i += 1

    return ''.join(result)


def extract_block(source: str, start_pos: int) -> tuple[str, int]:
    """Starting from an opening brace at start_pos, extract everything up to
    the matching closing brace.

    Returns (block_content, end_pos) where block_content is the text between
    the braces (exclusive) and end_pos is the position of the closing brace.

    Skips braces inside strings and comments.
    """
    if start_pos >= len(source) or source[start_pos] != '{':
        return "", start_pos

    depth = 1
    i = start_pos + 1
    length = len(source)

    while i < length and depth > 0:
        c = source[i]

        # Skip line comments
        if c == '/' and i + 1 < length and source[i + 1] == '/':
            i += 2
            while i < length and source[i] != '\n':
                i += 1
            continue

        # Skip block comments
        if c == '/' and i + 1 < length and source[i + 1] == '*':
            i += 2
            while i < length:
                if source[i] == '*' and i + 1 < length and source[i + 1] == '/':
                    i += 2
                    break
                i += 1
            continue

        # Skip double-quoted strings
        if c == '"':
            i += 1
            while i < length:
                if source[i] == '\\' and i + 1 < length:
                    i += 2
                    continue
                if source[i] == '"':
                    i += 1
                    break
                i += 1
            continue

        # Skip single-quoted strings
        if c == "'":
            i += 1
            while i < length:
                if source[i] == '\\' and i + 1 < length:
                    i += 2
                    continue
                if source[i] == "'":
                    i += 1
                    break
                i += 1
            continue

        # Skip backtick strings (raw strings in Go, template literals in TS)
        if c == '`':
            i += 1
            while i < length and source[i] != '`':
                i += 1
            if i < length:
                i += 1
            continue

        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return source[start_pos + 1:i], i

        i += 1

    # Unmatched — return what we have
    return source[start_pos + 1:i], i


def find_blocks(cleaned: str, pattern: str, original: str | None = None) -> list[tuple[int, str, str]]:
    """Find all occurrences of pattern followed by a brace block.

    Args:
        cleaned: Source with comments/strings stripped (for pattern matching).
        pattern: Regex pattern to search for in cleaned source.
        original: Original source for block extraction (if None, uses cleaned).

    Returns [(line_number, matched_header, block_content), ...]
    Block content is extracted from `original` (or `cleaned` if original is None)
    using token-aware brace matching.
    """
    src = original if original is not None else cleaned
    results = []
    regex = re.compile(pattern, re.MULTILINE)

    for m in regex.finditer(cleaned):
        header = m.group(0)
        line_number = cleaned[:m.start()].count('\n') + 1

        # Find the opening brace after the match
        pos = m.end()
        while pos < len(src) and src[pos] in ' \t\n\r':
            pos += 1

        if pos < len(src) and src[pos] == '{':
            block_content, end_pos = extract_block(src, pos)
            results.append((line_number, header, block_content))

    return results
