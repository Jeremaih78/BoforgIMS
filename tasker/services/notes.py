"""Shared optimistic-concurrency contract for task notes; no schema dependency."""

import hashlib


def normalize_notes(value):
    """Textareas and form submissions must agree on newline representation."""
    return value.replace("\r\n", "\n").replace("\r", "\n")


def notes_version(value):
    return hashlib.sha256(normalize_notes(value).encode("utf-8")).hexdigest()


class NotesConflictError(ValueError):
    def __init__(self, current_notes):
        self.notes = normalize_notes(current_notes)
        self.version = notes_version(current_notes)
        super().__init__(
            "Shared task notes changed since this page was loaded. "
            "Compare the saved notes with your draft before saving again."
        )
