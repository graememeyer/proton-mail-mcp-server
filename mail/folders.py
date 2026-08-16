"""
Mapping between friendly folder names and the mailbox layout Proton Bridge
presents over IMAP.

Bridge exposes Proton's system folders at the top level (``INBOX``, ``Sent``,
``Archive``, ``Spam``, ``Trash``, ``All Mail``) and namespaces everything the
user made themselves under ``Folders/`` (exclusive, a message lives in one) or
``Labels/`` (non-exclusive, a message can carry several). Callers shouldn't have
to know that, so ``pick_folder`` accepts any reasonable spelling.

Everything here is pure string handling against a folder list, so it is unit
testable without a Bridge instance.
"""

__all__ = [
    "SENT_LIKE",
    "pick_folder",
    "is_sent_like",
    "FolderNotFound",
    "describe_folders",
]

from typing import Iterable, List, Optional

# Aliases -> the canonical name Bridge uses. Keys are lowercased and stripped of
# spaces/underscores by _normalise, so "All Mail", "allmail" and "all_mail" all
# land on the same entry.
_ALIASES = {
    "inbox": "INBOX",
    "sent": "Sent",
    "sentitems": "Sent",
    "sentmail": "Sent",
    "draft": "Drafts",
    "drafts": "Drafts",
    "archive": "Archive",
    "spam": "Spam",
    "junk": "Spam",
    "trash": "Trash",
    "bin": "Trash",
    "deleted": "Trash",
    "deleteditems": "Trash",
    "allmail": "All Mail",
    "all": "All Mail",
    "starred": "Starred",
}

# Folders where the interesting timestamp is when we sent the message rather
# than when it arrived. Drives SENTSINCE vs SINCE in search criteria.
SENT_LIKE = {"sent", "drafts"}


class FolderNotFound(LookupError):
    """Raised when a caller-supplied folder name matches nothing in the mailbox."""


def _normalise(name: str) -> str:
    return (name or "").strip().lower().replace(" ", "").replace("_", "")


def is_sent_like(folder: str) -> bool:
    """True if the folder holds outbound mail (Sent/Drafts)."""
    return _normalise(folder).split("/")[-1] in SENT_LIKE


def pick_folder(requested: str, available: Iterable[str]) -> str:
    """Resolve a caller-supplied folder name against the mailbox's real folders.

    Tried in order, so that an exact name always wins over a fuzzy match:
      1. exact match, case sensitive
      2. a known alias ("junk" -> "Spam"), matched case-insensitively
      3. case-insensitive match on the full path ("folders/work")
      4. case-insensitive match on the leaf name, so a user folder can be named
         as just "Work" instead of "Folders/Work"

    Raises FolderNotFound with the available names when nothing matches.
    """
    names = list(available)
    if not requested:
        return "INBOX"

    if requested in names:
        return requested

    wanted = _normalise(requested)

    alias = _ALIASES.get(wanted)
    if alias:
        for name in names:
            if _normalise(name) == _normalise(alias):
                return name

    for name in names:
        if _normalise(name) == wanted:
            return name

    # Leaf match: "Work" -> "Folders/Work". Prefer Folders/ over Labels/ when
    # both exist, since an exclusive folder is the more common intent.
    leaf_matches = [n for n in names if _normalise(n.split("/")[-1]) == wanted]
    if leaf_matches:
        leaf_matches.sort(key=lambda n: (not n.startswith("Folders/"), n))
        return leaf_matches[0]

    raise FolderNotFound(
        f"No folder matching {requested!r}. Available folders: "
        f"{', '.join(sorted(names)) or '(none)'}"
    )


def describe_folders(
    names: Iterable[str], counts: Optional[dict] = None
) -> str:
    """Render the folder list for the list_folders tool, grouped by kind."""
    system: List[str] = []
    folders: List[str] = []
    labels: List[str] = []

    for name in sorted(names):
        if name.startswith("Folders/"):
            folders.append(name)
        elif name.startswith("Labels/"):
            labels.append(name)
        else:
            system.append(name)

    def render(group: List[str]) -> str:
        out = ""
        for name in group:
            stat = (counts or {}).get(name)
            if stat:
                out += (
                    f"  {name} ({stat.get('MESSAGES', '?')} messages, "
                    f"{stat.get('UNSEEN', '?')} unread)\n"
                )
            else:
                out += f"  {name}\n"
        return out

    sections = ""
    if system:
        sections += f"System folders:\n{render(system)}"
    if folders:
        sections += f"\nYour folders (a message lives in exactly one):\n{render(folders)}"
    if labels:
        sections += f"\nYour labels (a message can carry several):\n{render(labels)}"
    return sections or "No folders found."
