from __future__ import annotations

import re

EMAIL_PATTERN = re.compile(r"[A-Z0-9._%+'-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
GMAIL_STYLE_DOMAINS = {"gmail.com", "googlemail.com"}


def normalize_address(address: str) -> str:
    return address.strip().casefold()


def recipient_key(address: str) -> str:
    """Normalise only provider behaviours that are documented and deterministic."""

    value = normalize_address(address)
    local, separator, domain = value.rpartition("@")
    if not separator:
        return value
    if domain in GMAIL_STYLE_DOMAINS:
        local = local.split("+", 1)[0].replace(".", "")
        domain = "gmail.com"
    return f"{local}@{domain}"


def is_github_noreply(address: str) -> bool:
    _, separator, domain = normalize_address(address).rpartition("@")
    return bool(separator and domain == "users.noreply.github.com")
