"""Email extraction utility for Jira descriptions and Zendesk ticket bodies."""
import re

EMAIL_RE = re.compile(r"[\w.\-+]+@[\w\-]+(?:\.[\w\-]+)+")

INTERNAL_DOMAINS: frozenset[str] = frozenset({"eldiario.es"})


def extract_emails(
    text: str | None,
    exclude_domains: frozenset[str] | set[str] = frozenset(),
) -> list[str]:
    if not text:
        return []
    raw = EMAIL_RE.findall(text)
    out: set[str] = set()
    for e in raw:
        norm = e.lower().strip(".,;:)")
        if "@" not in norm:
            continue
        domain = norm.rsplit("@", 1)[1]
        if exclude_domains and domain in exclude_domains:
            continue
        out.add(norm)
    return sorted(out)
