"""A personal corpus for the daily harness: a folder of notes, documents and code, copied into a
store and indexed the way the v4 grid indexes C-rho (one level, rho 1). Read-only on the folder.

Store layout (~/.clp/<name>/ unless --store says otherwise):

    corpus.json        the folder, each file's stored path, sha256 and size, when it was indexed
    files/<path>       the text of each file as the index saw it (reads hash-check against it)
    index.sqlite       BM25 chunks (eval.corpus.build_index), table chunks_1
    address-v4.sqlite  the address index (index.build_address_index), level 1
    ledgers/           one CLP ledger per `clp ask` or `clp chat` session
    answers.jsonl      one line per question: the answer or abstention, citations, gate, cost

Stored paths are the folder-relative paths with "%", whitespace, ":" and "#" percent-encoded,
because an address "path:start-end#sha" cannot hold them ("00 Positions Index.md" is stored as
"00%20Positions%20Index.md"); `display_path` turns one back.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

from ..eval.corpus import CorpusIndex, build_index, corpus_id
from ..index import INDEX_FILE, AddressIndex, build_address_index

EXTENSIONS = (".md", ".txt", ".py")
MAX_FILE_BYTES = 512 * 1024
SKIP_DIRS = {"node_modules", "__pycache__"}  # and every directory whose name starts with "." (.obsidian, .git, .trash, .venv)
LEVEL = 1  # the one rho level of a personal corpus
MANIFEST = "corpus.json"
_ENCODE = re.compile(r"[%:#\s]")


class CorpusMissing(Exception):
    """No indexed corpus in the store, or the folder has nothing to index."""


def encode_path(relative: str) -> str:
    return _ENCODE.sub(lambda m: "".join(f"%{b:02X}" for b in m.group(0).encode("utf-8")), relative)


def display_path(stored: str) -> str:
    return unquote(stored)


def scan(folder: Path, *stores: Path | None) -> tuple[dict[str, str], list[str]]:
    """(stored path -> text, skipped relative paths) of every file under `folder` with one of
    EXTENSIONS, at most MAX_FILE_BYTES and valid UTF-8. Hidden directories and files are
    skipped, and symlinks are never followed. A file that cannot be read is skipped, and so is each
    of `stores` that sits inside `folder` (a store's copies are not the folder's files)."""
    texts: dict[str, str] = {}
    skipped: list[str] = []
    own = {store.expanduser().resolve() for store in stores if store is not None}
    for root, dirs, files in os.walk(folder, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS and Path(root, d).resolve() not in own)
        for name in sorted(files):
            path = Path(root) / name
            if name.startswith(".") or not name.endswith(EXTENSIONS) or path.is_symlink():
                continue
            relative = path.relative_to(folder).as_posix()
            try:
                text = path.read_text(encoding="utf-8") if path.stat().st_size <= MAX_FILE_BYTES else None
            except (UnicodeDecodeError, OSError):  # not text, or unreadable / gone mid-scan
                text = None
            if text is None:
                skipped.append(relative)
            elif text.strip():  # a blank file has nothing to read or cite
                texts[encode_path(relative)] = text
    return texts, skipped


def build(store: Path, folder: Path, name: str, home: Path | None = None) -> dict:
    """Index `folder` into `store` (first time or refresh) and return what changed: added,
    changed and removed stored paths, skipped files and the new manifest. Both indexes are
    rebuilt from the copied texts; nothing is written to `folder`. `home` is the CLP store that
    holds `store` beside other corpora and session files; none of it is indexed either."""
    folder = folder.expanduser().resolve()
    if not folder.is_dir():
        raise CorpusMissing(f"{folder} is not a folder")
    texts, skipped = scan(folder, store, home)
    if not texts:
        raise CorpusMissing(f"no {', '.join(EXTENSIONS)} files under {folder}")
    old = load_manifest(store)["files"] if (store / MANIFEST).exists() else {}
    shas = {path: hashlib.sha256(text.encode("utf-8")).hexdigest() for path, text in texts.items()}
    added = sorted(p for p in texts if p not in old)
    changed = sorted(p for p in texts if p in old and old[p]["sha256"] != shas[p])
    removed = sorted(p for p in old if p not in texts)
    # copy the texts the index reads: write new and changed files, delete removed ones
    files = store / "files"
    for path in [*added, *changed]:
        target = files / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(texts[path], encoding="utf-8")
    for path in removed:
        (files / path).unlink(missing_ok=True)
    # rebuild the BM25 chunks and the address index over the whole folder (seconds for a vault)
    order = sorted(texts)
    build_index(store, texts, order, {LEVEL: len(order)}, lambda message: None)
    ident = corpus_id({"folder": str(folder), "files": shas})
    build_address_index(texts, {p: LEVEL for p in order}, [LEVEL], store / INDEX_FILE, corpus_id=ident)
    manifest = {
        "name": name, "folder": str(folder), "corpus_id": ident, "extensions": list(EXTENSIONS),
        "indexed_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "files": {p: {"sha256": shas[p], "bytes": len(texts[p].encode("utf-8"))} for p in order}, "skipped": skipped,
    }  # fmt: skip
    tmp = store / (MANIFEST + ".tmp")
    tmp.write_text(json.dumps(manifest, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    tmp.replace(store / MANIFEST)
    return {"added": added, "changed": changed, "removed": removed, "skipped": skipped, "manifest": manifest}


def load_manifest(store: Path) -> dict:
    path = store / MANIFEST
    if not path.exists():
        raise CorpusMissing(f"no indexed corpus in {store}; run `clp init <folder>` first")
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass
class DailyCorpus:
    """An indexed store opened for questions: BM25 search, the address index and the paths."""

    store: Path
    manifest: dict
    index: CorpusIndex
    address: AddressIndex

    @classmethod
    def open(cls, store: Path) -> DailyCorpus:
        manifest = load_manifest(store)
        if not (store / "files").is_dir():
            raise CorpusMissing(f"{store} has no copied files; run `clp refresh`")
        return cls(store, manifest, CorpusIndex(store, LEVEL), AddressIndex(store / INDEX_FILE, LEVEL))

    @property
    def paths(self) -> list[str]:
        return sorted(self.manifest["files"])

    def close(self) -> None:
        self.index.close()
        self.address.close()


__all__ = ["EXTENSIONS", "CorpusMissing", "DailyCorpus", "build", "display_path", "encode_path", "load_manifest", "scan"]
