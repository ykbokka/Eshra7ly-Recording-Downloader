"""Keep signed-URL tokens and credentials out of console output and logs."""
import re
from urllib.parse import urlsplit, urlunsplit

REDACTED = "<redacted>"
_URL_RE = re.compile(r"https?://[^\s'\"<>]+", re.I)
_SECRET_LINE = re.compile(r"(?im)\b(authorization|cookie|set-cookie|proxy-authorization)\b\s*[:=]\s*[^\r\n]*")


def _kv(piece: str) -> str:
    return piece.partition("=")[0] + "=" + REDACTED if "=" in piece else piece


def redact_url(url: str) -> str:
    try:
        p = urlsplit(url)
        host = p.hostname or ""
        if p.port:
            host += f":{p.port}"
    except ValueError:
        return "<invalid-url>"
    segs = []
    for seg in p.path.split("/"):
        if "=" in seg:  # signed path segments like exp=...~hmac=...
            seg = "~".join(_kv(x) for x in seg.split("~"))
        segs.append(seg)
    query = "&".join(_kv(x) for x in p.query.split("&")) if p.query else ""
    return urlunsplit((p.scheme, host, "/".join(segs), query, ""))


def redact_text(text: str) -> str:
    text = _SECRET_LINE.sub(lambda m: f"{m.group(1)}: {REDACTED}", text or "")
    return _URL_RE.sub(lambda m: redact_url(m.group(0)), text)


def redact_command(args) -> str:
    out, hide = [], False
    for a in args:
        if hide:
            out.append(REDACTED)
            hide = False
        elif a == "-headers":
            out.append(a)
            hide = True
        else:
            out.append(redact_text(a))
    return " ".join(f'"{a}"' if " " in a else a for a in out)
