"""The daily harness: the paper's L4 working set (address index, answer gate, targeted repair)
over a personal folder of notes, documents and code, as the `clp` command (daily/cli.py)."""

from .corpus import CorpusMissing, DailyCorpus, build, display_path, encode_path, scan
from .episode import DAILY_PROMPT_VERSION, DailyAnswer, DailyEpisode, daily_config, describe, find_note, note_name

__all__ = [
    "DAILY_PROMPT_VERSION", "CorpusMissing", "DailyAnswer", "DailyCorpus", "DailyEpisode", "build", "daily_config", "describe", "display_path",
    "encode_path", "find_note", "note_name", "scan",
]  # fmt: skip
