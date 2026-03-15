"""Account slug derivation from target_account display name."""

from __future__ import annotations


def _has_cjk(text: str) -> bool:
    """Return True if text contains CJK characters."""
    return any("\u4e00" <= c <= "\u9fff" for c in (text or ""))


def target_account_to_slug(account: str) -> str:
    """Derive target_account_slug from target_account.

    - If target_account contains Chinese: return pinyin (lowercase, concatenated).
    - Otherwise: return target_account as-is.
    """
    raw = (account or "").strip()
    if not raw:
        return ""
    if not _has_cjk(raw):
        return raw
    from pypinyin import lazy_pinyin

    parts = lazy_pinyin(raw)
    return "".join(p for p in parts if isinstance(p, str)).lower()
