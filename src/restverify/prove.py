"""`restverify prove` (I13, R48) — prove a snapshot by restoring a sample.

Logic only: every restic invocation happens in restic.py (Single Spawner rule),
every filesystem write happens in tempstore-controlled dirs.

The verification has two layers, and the module docstring states both:

1. Sampled-file check (existence + size) against restic's own `ls --json`
   listing. SIZE-ONLY per file: ls exposes no content hashes
   (measured on restic 0.16.4), so per-file content comparison without a
   source is impossible. It still bites: with one flipped byte in a real
   pack, `restic restore` exits 0 writing a 0-byte file where the listing
   says 12 bytes — this layer catches exactly that.

2. Content integrity over the same share: `restic check --read-data-subset
   N%` reads that share of the repository's data packs and verifies the
   repository's cryptographic seals (100% → full --read-data). Measured: a
   flipped byte leaves restore exiting 0 while check reports "repository
   contains errors" — this layer catches corruptions the size check cannot
   see (e.g. a zero-for-zero byte swap).

The original build shipped layer 1 only; review asked why, and layer 2 was
added in this same commit series.

Sampling is deterministic: the same (file list, percent, seed) always yields
the same sample. The default seed is derived from the snapshot id, so runs are
reproducible per snapshot; --seed overrides. The DRBG is counter-mode AES-256
implemented on the standard library only (no runtime dependency, C1 holds) and
pinned in the test suite against the official FIPS-197 block vectors and NIST
SP 800-38A CTR vectors.
"""
from __future__ import annotations

import argparse
import hashlib
import struct
from pathlib import Path

from . import __version__, SCHEMA_VERSION
from . import excludes as excludesmod

PROG = "restverify"
DEFAULT_PERCENT = 10
MAX_PERCENT = 100
LARGEST_ALWAYS = True


# ── AES-256 (FIPS-197), stdlib-only, for the sampling DRBG ────────────────

_SBOX = [
    0x63, 0x7C, 0x77, 0x7B, 0xF2, 0x6B, 0x6F, 0xC5, 0x30, 0x01, 0x67, 0x2B, 0xFE, 0xD7, 0xAB, 0x76,
    0xCA, 0x82, 0xC9, 0x7D, 0xFA, 0x59, 0x47, 0xF0, 0xAD, 0xD4, 0xA2, 0xAF, 0x9C, 0xA4, 0x72, 0xC0,
    0xB7, 0xFD, 0x93, 0x26, 0x36, 0x3F, 0xF7, 0xCC, 0x34, 0xA5, 0xE5, 0xF1, 0x71, 0xD8, 0x31, 0x15,
    0x04, 0xC7, 0x23, 0xC3, 0x18, 0x96, 0x05, 0x9A, 0x07, 0x12, 0x80, 0xE2, 0xEB, 0x27, 0xB2, 0x75,
    0x09, 0x83, 0x2C, 0x1A, 0x1B, 0x6E, 0x5A, 0xA0, 0x52, 0x3B, 0xD6, 0xB3, 0x29, 0xE3, 0x2F, 0x84,
    0x53, 0xD1, 0x00, 0xED, 0x20, 0xFC, 0xB1, 0x5B, 0x6A, 0xCB, 0xBE, 0x39, 0x4A, 0x4C, 0x58, 0xCF,
    0xD0, 0xEF, 0xAA, 0xFB, 0x43, 0x4D, 0x33, 0x85, 0x45, 0xF9, 0x02, 0x7F, 0x50, 0x3C, 0x9F, 0xA8,
    0x51, 0xA3, 0x40, 0x8F, 0x92, 0x9D, 0x38, 0xF5, 0xBC, 0xB6, 0xDA, 0x21, 0x10, 0xFF, 0xF3, 0xD2,
    0xCD, 0x0C, 0x13, 0xEC, 0x5F, 0x97, 0x44, 0x17, 0xC4, 0xA7, 0x7E, 0x3D, 0x64, 0x5D, 0x19, 0x73,
    0x60, 0x81, 0x4F, 0xDC, 0x22, 0x2A, 0x90, 0x88, 0x46, 0xEE, 0xB8, 0x14, 0xDE, 0x5E, 0x0B, 0xDB,
    0xE0, 0x32, 0x3A, 0x0A, 0x49, 0x06, 0x24, 0x5C, 0xC2, 0xD3, 0xAC, 0x62, 0x91, 0x95, 0xE4, 0x79,
    0xE7, 0xC8, 0x37, 0x6D, 0x8D, 0xD5, 0x4E, 0xA9, 0x6C, 0x56, 0xF4, 0xEA, 0x65, 0x7A, 0xAE, 0x08,
    0xBA, 0x78, 0x25, 0x2E, 0x1C, 0xA6, 0xB4, 0xC6, 0xE8, 0xDD, 0x74, 0x1F, 0x4B, 0xBD, 0x8B, 0x8A,
    0x70, 0x3E, 0xB5, 0x66, 0x48, 0x03, 0xF6, 0x0E, 0x61, 0x35, 0x57, 0xB9, 0x86, 0xC1, 0x1D, 0x9E,
    0xE1, 0xF8, 0x98, 0x11, 0x69, 0xD9, 0x8E, 0x94, 0x9B, 0x1E, 0x87, 0xE9, 0xCE, 0x55, 0x28, 0xDF,
    0x8C, 0xA1, 0x89, 0x0D, 0xBF, 0xE6, 0x42, 0x68, 0x41, 0x99, 0x2D, 0x0F, 0xB0, 0x54, 0xBB, 0x16,
]
_INV_SBOX = [0] * 256
for _i, _v in enumerate(_SBOX):
    _INV_SBOX[_v] = _i

_RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36, 0x6C, 0xD8, 0xAB, 0x4D]


def _xtime(a: int) -> int:
    a <<= 1
    if a & 0x100:
        a = (a ^ 0x1B) & 0xFF
    return a


def _mul(a: int, b: int) -> int:
    """Multiply in GF(2^8) (FIPS-197 §4.2)."""
    result = 0
    for _ in range(8):
        if b & 1:
            result ^= a
        b >>= 1
        a = _xtime(a)
    return result


def _key_expansion(key: bytes) -> list[list[int]]:
    """AES-256 key schedule: 60 words (FIPS-197 §5.3.4; Nk=8, Nr=14)."""
    nk, nr = 8, 14
    words = [list(key[4 * i:4 * i + 4]) for i in range(nk)]
    for i in range(nk, 4 * (nr + 1)):
        temp = list(words[i - 1])
        if i % nk == 0:
            temp = temp[1:] + temp[:1]
            temp = [_SBOX[b] for b in temp]
            temp[0] ^= _RCON[i // nk - 1]
        elif i % nk == 4:
            temp = [_SBOX[b] for b in temp]
        words.append([a ^ b for a, b in zip(words[i - nk], temp)])
    return [words[4 * r:4 * r + 4] for r in range(nr + 1)]  # round keys


def _add_round_key(state, round_key) -> None:
    for c in range(4):
        for r in range(4):
            state[r][c] ^= round_key[c][r]


def _sub_bytes(state, box) -> None:
    for r in range(4):
        for c in range(4):
            state[r][c] = box[state[r][c]]


def _shift_rows(state) -> None:
    for r in range(1, 4):
        state[r] = state[r][r:] + state[r][:r]


def _inv_shift_rows(state) -> None:
    for r in range(1, 4):
        state[r] = state[r][-r:] + state[r][:-r]


def _mix_columns(state) -> None:
    for c in range(4):
        a = [state[r][c] for r in range(4)]
        state[0][c] = _mul(a[0], 2) ^ _mul(a[1], 3) ^ a[2] ^ a[3]
        state[1][c] = a[0] ^ _mul(a[1], 2) ^ _mul(a[2], 3) ^ a[3]
        state[2][c] = a[0] ^ a[1] ^ _mul(a[2], 2) ^ _mul(a[3], 3)
        state[3][c] = _mul(a[0], 3) ^ a[1] ^ a[2] ^ _mul(a[3], 2)


def _inv_mix_columns(state) -> None:
    for c in range(4):
        a = [state[r][c] for r in range(4)]
        state[0][c] = _mul(a[0], 14) ^ _mul(a[1], 11) ^ _mul(a[2], 13) ^ _mul(a[3], 9)
        state[1][c] = _mul(a[0], 9) ^ _mul(a[1], 14) ^ _mul(a[2], 11) ^ _mul(a[3], 13)
        state[2][c] = _mul(a[0], 13) ^ _mul(a[1], 9) ^ _mul(a[2], 14) ^ _mul(a[3], 11)
        state[3][c] = _mul(a[0], 11) ^ _mul(a[1], 13) ^ _mul(a[2], 9) ^ _mul(a[3], 14)


# T-tables (SubBytes+ShiftRows+MixColumns fused): the classic fast software
# formulation, so one block costs table lookups instead of GF multiplies —
# a 100k-file sample needs thousands of blocks, and the naive per-byte GF
# multiply version measured too slow to be usable. Correctness is still
# pinned by the same FIPS-197 / SP 800-38A vectors.

def _build_ttables():
    """te_k[x] = the MixColumns matrix column for the byte at row k, packed in
    the word's byte slots: te_k is a one-byte rotation of te_{k-1}
    ([2S,S,S,3S] -> [3S,2S,S,S] -> [S,3S,2S,S] -> [S,S,3S,2S]). Getting the 3S
    slot wrong in any of them fails the FIPS-197 vectors, which is exactly how
    the first draft was caught."""
    te0, te1, te2, te3 = [], [], [], []
    for x in range(256):
        s = _SBOX[x]
        s2 = _xtime(s)          # 2S in GF(2^8)
        s3 = s2 ^ s             # 3S = 2S xor S
        te0.append((s2 << 24) | (s << 16) | (s << 8) | s3)
        te1.append((s3 << 24) | (s2 << 16) | (s << 8) | s)
        te2.append((s << 24) | (s3 << 16) | (s2 << 8) | s)
        te3.append((s << 24) | (s << 16) | (s3 << 8) | s2)
    return te0, te1, te2, te3


_TE0, _TE1, _TE2, _TE3 = _build_ttables()


class _Aes256:
    """AES-256 block cipher with the key schedule expanded once per key."""

    def __init__(self, key: bytes):
        if len(key) != 32:
            raise ValueError("AES-256 key must be 32 bytes")
        rounds = _key_expansion(key)
        # pack each round key's columns into big-endian words: word[c] =
        # round_key[c][0]<<24 | [1]<<16 | [2]<<8 | [3]
        self._rk = [[(w[0] << 24) | (w[1] << 16) | (w[2] << 8) | w[3]
                     for w in round] for round in rounds]

    def encrypt_block(self, block: bytes) -> bytes:
        rk = self._rk
        w = [int.from_bytes(block[4 * c:4 * c + 4], "big") ^ rk[0][c]
             for c in range(4)]
        te0, te1, te2, te3 = _TE0, _TE1, _TE2, _TE3
        for round_ in range(1, 14):
            nrk = rk[round_]
            w = [
                (te0[(w[c] >> 24) & 0xFF]
                 ^ te1[(w[(c + 1) % 4] >> 16) & 0xFF]
                 ^ te2[(w[(c + 2) % 4] >> 8) & 0xFF]
                 ^ te3[w[(c + 3) % 4] & 0xFF]
                 ^ nrk[c])
                for c in range(4)]
        # final round: SubBytes + ShiftRows + AddRoundKey (no MixColumns)
        sbox = _SBOX
        nrk = rk[14]
        out = bytearray(16)
        for c in range(4):
            word = ((sbox[(w[c] >> 24) & 0xFF] << 24)
                    | (sbox[(w[(c + 1) % 4] >> 16) & 0xFF] << 16)
                    | (sbox[(w[(c + 2) % 4] >> 8) & 0xFF] << 8)
                    | sbox[w[(c + 3) % 4] & 0xFF]) ^ nrk[c]
            out[4 * c:4 * c + 4] = word.to_bytes(4, "big")
        return bytes(out)


def aes256_encrypt_block(key: bytes, block: bytes) -> bytes:
    """One AES-256 block encryption (FIPS-197 §5.1, Nk=8, Nr=14)."""
    return _Aes256(key).encrypt_block(block)


def aes256_decrypt_block(key: bytes, block: bytes) -> bytes:
    state = [[block[r + 4 * c] for c in range(4)] for r in range(4)]
    round_keys = _key_expansion(key)
    _add_round_key(state, round_keys[14])
    for round_ in range(13, 0, -1):
        _inv_shift_rows(state)
        _sub_bytes(state, _INV_SBOX)
        _add_round_key(state, round_keys[round_])
        _inv_mix_columns(state)
    _inv_shift_rows(state)
    _sub_bytes(state, _INV_SBOX)
    _add_round_key(state, round_keys[0])
    return bytes(state[r][c] for c in range(4) for r in range(4))


def _keystream(key: bytes, counter_block: bytes, n_blocks: int):
    block = counter_block
    for _ in range(n_blocks):
        block = aes256_encrypt_block(key, block)
        yield block


# ── the DRBG (counter-mode, per the approved plan) ─────────────────────────

class SamplingDRBG:
    """Counter-mode AES-256-CTR DRBG on the stdlib only.

    key/nonce are 32/16 bytes (e.g. hashed from a snapshot id + seed), the
    counter is a 128-bit big-endian block that increments across the stream.
    Deterministic by construction; pinned against NIST SP 800-38A F.5.5.
    The cipher object is built once per key, and refills are chunked, so a
    100k-file sample costs one key expansion plus a few thousand table-driven
    block encryptions — measured well under a second.
    """

    def __init__(self, key: bytes, nonce: bytes):
        if len(key) != 32:
            raise ValueError("DRBG key must be 32 bytes (AES-256)")
        if len(nonce) != 16:
            raise ValueError("DRBG nonce must be 16 bytes (one CTR block)")
        self._cipher = _Aes256(key)
        self._block = nonce
        self._buffer = b""

    def _refill(self, n_bytes: int) -> None:
        need = n_bytes - len(self._buffer)
        if need > 0:
            blocks = max((need + 15) // 16, 512)  # chunked: fewer refills
            start = int.from_bytes(self._block, "big")
            counters = [(start + i).to_bytes(16, "big") for i in range(blocks)]
            self._buffer += b"".join(
                self._cipher.encrypt_block(c) for c in counters)
            self._block = (start + blocks).to_bytes(16, "big")

    def random_bytes(self, n: int) -> bytes:
        self._refill(n)
        out, self._buffer = self._buffer[:n], self._buffer[n:]
        return out

    def below(self, ceiling: int) -> int:
        """Uniform in [0, ceiling) via rejection sampling (no modulo bias).

        The drawn bytes are shifted right so only the top `bits` bits are
        kept — without that, a ceiling like 100000 (17 bits) would reject
        15 of every 16 whole-byte draws and sampling 10k files from 100k
        measured ~19s; with the shift it is ~0.1s.
        """
        if ceiling <= 1:
            return 0
        bits = (ceiling - 1).bit_length()
        nbytes = (bits + 7) // 8
        shift = nbytes * 8 - bits
        while True:
            value = int.from_bytes(self.random_bytes(nbytes), "big") >> shift
            if value < ceiling:
                return value


def derive_drbg(snapshot_id: str, seed: int | None, percent: int = 0
                ) -> tuple[bytes, bytes, int, str]:
    """Deterministic DRBG material from the snapshot id (+ optional seed).

    The percent is mixed into the material as domain separation, so the same
    snapshot with --sample 10 vs --sample 20 does not reuse one keystream."""
    material = snapshot_id.encode("utf-8") + b"\x00" + (
        b"" if seed is None else struct.pack(">Q", seed & 0xFFFFFFFFFFFFFFFF)) \
        + struct.pack(">H", percent & 0xFFFF)
    digest = hashlib.sha256(material).digest()
    key = hashlib.sha256(b"restverify-prove-key\x00" + material).digest()
    nonce = hashlib.sha256(b"restverify-probe-ctr\x00" + material).digest()[:16]
    effective = 0 if seed is None else seed
    return key, nonce, effective, (
        f"derived from snapshot id" if seed is None else f"--seed {effective}")


# ── parsing / resolution ──────────────────────────────────────────────────

def parse_percent(value) -> int:
    """--sample: integer 1-100 (default already applied by argparse)."""
    try:
        percent = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"--sample must be an integer percent (got {value!r})") from None
    if not 1 <= percent <= MAX_PERCENT:
        raise ValueError(f"--sample must be between 1 and 100 (got {percent})")
    return percent


def percent_arg(value) -> int:
    """argparse type= for --sample: a bad value is a USAGE problem (exit 64,
    never a verification outcome), raised before anything runs — the same
    pattern as webhook.url_arg, so the teaching text survives verbatim."""
    try:
        return parse_percent(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def resolve_seed(seed, snapshot_id: str) -> tuple[int, str]:
    """(effective_seed, description). Default derives from the snapshot id."""
    if seed is None:
        derived = int.from_bytes(hashlib.sha256(snapshot_id.encode()).digest()[:8], "big")
        return derived, f"derived from snapshot {snapshot_id[:8]}"
    return int(seed), f"--seed {int(seed)}"


# ── the file list ─────────────────────────────────────────────────────────

def file_list(nodes: list[dict]) -> list[dict]:
    """Files only ({path, size}), sorted by path; dirs/symlinks never sampled."""
    files = [{"path": node["path"], "size": int(node.get("size") or 0)}
             for node in nodes
             if node.get("type") == "file" and node.get("path")]
    files.sort(key=lambda item: item["path"])
    return files


def sample(files: list[dict], percent: int, seed: int,
           snapshot_id: str = "") -> list[dict]:
    """Deterministic sample of EXACTLY ceil(len(files) * percent / 100) files
    (min 1 when files exist), and the largest file (size, then path) is always
    one of them — it is the likeliest corruption canary, so it must never be
    left to chance. Same (files, percent, seed, snapshot id) always yields the
    same sample."""
    total = len(files)
    if total == 0:
        return []
    count = max(1, -(-(total * percent) // 100))      # ceil division
    if count >= total or percent >= MAX_PERCENT:
        return list(files)
    largest = max(range(total), key=lambda i: (files[i]["size"], files[i]["path"]))
    key, nonce, _effective, _desc = derive_drbg(snapshot_id, seed, percent)
    drbg = SamplingDRBG(key, nonce)
    chosen = {largest}
    while len(chosen) < count:
        index = drbg.below(total)
        if index != largest:                          # largest is already in
            chosen.add(index)
    return [files[i] for i in sorted(chosen)]


# ── verification ──────────────────────────────────────────────────────────

def _restore_suffixes(raw: str, snapshot_paths) -> list[str]:
    """Candidate path suffixes for a sample path, relative to restore_root.

    `restic restore --target <dir>` recreates the snapshot's paths *under* the
    target (restored_root() locates that subtree), so a file's restored
    location is the snapshot-path prefix stripped off. restic's strip rule is
    not uniform (POSIX paths keep their shape, a Windows drive path lands
    under its last component — measured, see restored_root), so every
    snapshot-path prefix is tried, longest (most specific) first.

    Two families of candidates, raw first: on POSIX a backslash is a legal
    FILENAME character (measured: a file named `back\\slash.txt` restores and
    verifies under that literal name), so the backslash-normalised variant is
    only a fallback for Windows-hosted snapshot paths, never the first try.
    The bare leading-slash strip is the last resort of each family.
    """
    posix = raw.replace("\\", "/")
    raw_suffixes: list[str] = []
    norm_suffixes: list[str] = []
    for sp in snapshot_paths or []:
        sp_raw = str(sp).rstrip("/")
        if raw.startswith(sp_raw + "/"):
            raw_suffixes.append(raw[len(sp_raw) + 1:])
        sp_norm = str(sp).replace("\\", "/").rstrip("/")
        if posix.startswith(sp_norm + "/"):
            norm_suffixes.append(posix[len(sp_norm) + 1:])
    raw_suffixes.sort(key=len, reverse=True)      # most specific prefix first
    norm_suffixes.sort(key=len, reverse=True)
    out: list[str] = []
    for suffix in raw_suffixes + norm_suffixes:
        if suffix not in out:
            out.append(suffix)
    for fallback in (raw.lstrip("/"), posix.lstrip("/")):
        if fallback not in out:
            out.append(fallback)
    return out


def verify_sample(sample: list[dict], restore_root,
                  snapshot_paths: list[str] | None = None) -> list[dict]:
    """Existence + size for every sampled file, under the snapshot's restored
    subtree. The restored location is the snapshot-path prefix stripped from
    the listing path (the same rule restored_root uses to find the subtree);
    every resolved path must stay inside restore_root (path-traversal guard:
    repository metadata cannot aim the check at arbitrary host files).
    """
    failures: list[dict] = []
    root = Path(restore_root)
    for item in sample:
        raw = item["path"]
        resolved = None
        for suffix in _restore_suffixes(raw, snapshot_paths):
            candidate = root.joinpath(*suffix.split("/"))
            try:
                resolved = candidate.resolve()
                resolved.relative_to(root)
                break                      # inside the restore area: this is the one
            except (OSError, ValueError):
                resolved = None            # escaped the area: try the next suffix
        if resolved is None:
            failures.append({"path": raw, "reason": "path escaped the restore area",
                             "expected_size": item["size"], "actual_size": None})
            continue
        if not resolved.exists():
            failures.append({"path": raw, "reason": "missing from the restore",
                             "expected_size": item["size"], "actual_size": None})
            continue
        if not resolved.is_file():
            failures.append({"path": raw, "reason": "not a regular file after restore",
                             "expected_size": item["size"], "actual_size": None})
            continue
        actual = resolved.stat().st_size
        if actual != item["size"]:
            failures.append({"path": raw, "reason": "size differs from the snapshot record",
                             "expected_size": item["size"], "actual_size": actual})
    return failures


# ── excludes ──────────────────────────────────────────────────────────────

def filter_excludes(items: list[dict], patterns) -> tuple[list[dict], int]:
    """Drop excluded paths from the sample BEFORE the restore, restic-style.

    Why in code rather than `restic restore --exclude`: prove restores exactly
    the paths it will verify, so the filtered set must be decided BEFORE the
    restore — if restic dropped a file we had already counted as sampled, a
    healthy repository would report a false mismatch. The matching honours
    restic's own retry rule (a pattern may match the full path or any
    leading-stripped suffix of it, and a file is excluded when any parent
    directory matches) via the shared ExcludeMatcher.

    Returns (kept, excluded_count).
    """
    if not patterns:
        return list(items), 0
    matcher = excludesmod.compile_matcher(patterns)
    kept: list[dict] = []
    excluded = 0
    for item in items:
        # No backslash normalisation here: on POSIX a backslash is a filename
        # character, and restic's own filter would treat it as one too.
        rel = str(item["path"]).strip("/")
        parts = rel.split("/") if rel else []
        if any(matcher.matches("/".join(parts[i:])) for i in range(len(parts))):
            excluded += 1
            continue
        kept.append(item)
    return kept, excluded


# ── envelopes ─────────────────────────────────────────────────────────────

def dry_run_lines(repo: str, percent, seed, selector: str) -> list[str]:
    return [
        "dry run — nothing will be restored",
        f"  repo     : {repo}",
        f"  snapshot : {selector}",
        f"  sample   : {percent}% of the snapshot's files (deterministic; default seed "
        f"derived from the snapshot id)",
        f"  verify   : existence + size of every sampled file (restic ls exposes no "
        f"per-file hashes), plus restic check --read-data-subset over the same "
        f"share of data packs",
        "✓ PASS (dry run) — nothing was restored, nothing was recorded",
    ]


def dry_run_payload(repo: str, percent, seed, selector: str) -> dict:
    return {
        "tool": PROG,
        "schema": SCHEMA_VERSION,
        "version": __version__,
        "command": "prove",
        "status": "dry_run",
        "exit_code": 0,
        "dry_run": {
            "repo": repo,
            "snapshot": selector,
            "sample_percent": percent,
            "seed": seed,
            "restores_anything": False,
            "restic_invoked": False,
        },
    }


def payload(entry, snapshot, files: list[dict], percent: int, seed: int, seed_desc: str,
            sample: list[dict], failures: list[dict], history_block: dict,
            restore_root, cleaned: bool, exit_code: int, excludes: list[str],
            files_excluded: int = 0, check_ok: bool = True,
            check_line: str = "") -> dict:
    verified = len(sample) - len(failures)
    return {
        "tool": PROG,
        "schema": SCHEMA_VERSION,
        "version": __version__,
        "command": "prove",
        "status": "pass" if (not failures and check_ok) else "diff_mismatch",
        "exit_code": exit_code,
        "history": history_block,
        "snapshot": {
            "id": snapshot.id,
            "short_id": snapshot.short_id,
            "time": snapshot.time,
            "paths": snapshot.paths,
        },
        "prove": {
            "files_listed": len(files),
            "files_sampled": len(sample),
            "files_verified": verified,
            "files_failed": len(failures),
            "hashes_available": False,
            "content_check": {
                "performed": bool(sample),
                "ok": check_ok,
                "detail": check_line,
                "note": "restic check --read-data-subset verifies the repository's "
                        "cryptographic seals over the same share of data packs",
            },
            "percent": percent,
            "seed": seed,
            "seed_desc": seed_desc,
            "failures": failures,
            "excludes": list(excludes or []),
            "files_excluded": int(files_excluded),
        },
        "restore": {
            "repo": entry.repo,
            "target": str(restore_root) if restore_root is not None else None,
            "target_removed": bool(cleaned),
        },
    }
