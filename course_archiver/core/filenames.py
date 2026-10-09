import re
import unicodedata

_MAP = {":": " -", "/": "-", "\\": "-", "|": "-", '"': "'", "<": "", ">": "", "?": "", "*": ""}
_BAD = re.compile(r"[\x00-\x08\x0e-\x1f\x7f]")
_INVIS = re.compile("[​-‏‪-‮⁦-⁩﻿]")
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def sanitize_filename(name, max_length=100, fallback="recording"):
    t = unicodedata.normalize("NFC", name or "")
    t = _INVIS.sub("", _BAD.sub("", t))
    for a, b in _MAP.items():
        t = t.replace(a, b)
    t = re.sub(r"\s+", " ", t).strip(" .")[:max_length].rstrip(" .")
    if t.split(".")[0].strip().upper() in _RESERVED:
        t = "_" + t
    return t or fallback
