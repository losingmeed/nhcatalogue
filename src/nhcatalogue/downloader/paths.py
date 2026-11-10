"""Folder templating and path sanitization for the library tree (plan §4).

Default template: ``{artist}/{id} - {title}`` → ``library/<artist>/<id> - <title>/``
Vars: {artist} {parody} {character} {language} {id} {title}; multi-artist uses
the primary artist. Illegal characters are replaced, length is capped, names
never end in dots/spaces (Windows safety).
"""

from __future__ import annotations

import re
import unicodedata

# Windows + Linux illegal chars, reserved device names, control chars
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_DOTS_SPACES = re.compile(r"[ .]+$")
RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
MAX_COMPONENT = 120  # per path component; NTFS limit is 255, leave headroom


def sanitize_component(name: str, *, max_len: int = MAX_COMPONENT) -> str:
    """Sanitize one path component (folder or file name)."""
    name = unicodedata.normalize("NFC", str(name or ""))
    name = _ILLEGAL.sub(" ", name)
    # strip newlines/tabs and collapse whitespace
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        name = "unknown"
    if name.lower() in RESERVED:
        name = f"_{name}"
    if len(name) > max_len:
        name = name[:max_len].rstrip(" .")
    return name


TEMPLATE_VARS = ("artist", "parody", "character", "language", "id", "title")
_VAR_RE = re.compile(r"\{(\w+)\}")


def render_template(template: str, variables: dict[str, str]) -> str:
    """Render a folder template with {vars}; missing vars → 'unknown'.

    Ensures the result is relative, has no '..' and no empty components.
    """
    resolved = {
        key: sanitize_component(str(value if value not in (None, "") else "unknown"))
        for key, value in variables.items()
        if key in TEMPLATE_VARS
    }

    def sub(m: re.Match) -> str:
        return resolved.get(m.group(1), "unknown")

    out = _VAR_RE.sub(sub, template or "{id} - {title}")
    # normalize separators and drop empties / traversal
    parts = [p for p in re.split(r"[/\\]+", out) if p.strip() and p.strip() != "."]
    parts = [sanitize_component(p) for p in parts]
    if not parts:
        parts = ["unknown"]
    return "/".join(parts)


def page_filename(page_number: int, ext: str, total_pages: int) -> str:
    """Zero-padded page file with the original extension: 001.jpg, 002.png."""
    pad = max(3, len(str(total_pages)))
    ext = (ext or "jpg").lstrip(".").lower()
    return f"{page_number:0{pad}d}.{ext}"
