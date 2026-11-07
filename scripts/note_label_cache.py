"""Authenticated encryption for note-label cache bytes, never raw secrets."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from typing import Any, Mapping

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

CONTEXT = b"notion-diary/notes-label-cache/v1"
NONCE_BYTES = 12


def _key(purpose: bytes) -> bytes:
    # The existing high-entropy API credential is only key material in memory.
    # Domain-separated derivation; neither it nor the derived key is persisted.
    material = os.getenv("OPENAI_API_KEY", "").strip()
    if not material:
        raise RuntimeError("note_cache_encryption_key_unavailable")
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=CONTEXT + purpose).derive(material.encode())


def cache_identifier(identity: str) -> str:
    return hmac.new(_key(b"/index"), identity.encode(), hashlib.sha256).hexdigest() + ".json"


def encode_cache(payload: Mapping[str, Any], *, context: str) -> bytes:
    nonce = os.urandom(NONCE_BYTES)
    ciphertext = AESGCM(_key(b"/aead")).encrypt(nonce, json.dumps(payload, ensure_ascii=False).encode(), CONTEXT + context.encode())
    return json.dumps({"version": 1, "nonce": base64.b64encode(nonce).decode(),
                       "ciphertext": base64.b64encode(ciphertext).decode()}).encode()


def decode_cache(blob: bytes, *, context: str) -> dict[str, Any]:
    envelope = json.loads(blob)
    if not isinstance(envelope, dict) or envelope.get("version") != 1:
        raise ValueError("encrypted_note_cache_required")
    nonce = base64.b64decode(envelope["nonce"], validate=True)
    if len(nonce) != NONCE_BYTES:
        raise ValueError("invalid_cache_nonce")
    ciphertext = base64.b64decode(envelope["ciphertext"], validate=True)
    plaintext = AESGCM(_key(b"/aead")).decrypt(nonce, ciphertext, CONTEXT + context.encode())
    payload = json.loads(plaintext)
    if not isinstance(payload, dict):
        raise ValueError("invalid_cache_payload")
    return payload
