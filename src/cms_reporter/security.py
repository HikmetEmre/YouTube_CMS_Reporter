"""Encryption: one master key in the environment, everything else derived from it.

- Client deliverables are protected with a per-client password (stable across
  months) so a client needs to be told it only once. Excel workbooks use Office
  password protection, CSVs travel in an AES-256 zip, PDFs use PDF encryption -
  all open with ordinary desktop software.
- Internal files and the history store use keys derived from the same master key,
  so nothing sensitive is written to disk in the clear.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import os
import secrets
from pathlib import Path

import msoffcrypto
import pyzipper
from cryptography.fernet import Fernet
from msoffcrypto.format.ooxml import OOXMLFile

MASTER_KEY_ENV = "CMS_MASTER_KEY"
INTERNAL = "internal"


class KeyError_(RuntimeError):
    pass


def generate_master_key() -> str:
    return secrets.token_urlsafe(32)


def master_key() -> bytes:
    key = os.environ.get(MASTER_KEY_ENV, "").strip()
    if len(key) < 24:
        raise KeyError_(
            f"{MASTER_KEY_ENV} is not set (or is shorter than 24 characters). "
            "Generate one with `cms-reporter keygen` and put it in your .env file. "
            "Keep it safe: it is the only way to open previously encrypted files."
        )
    return key.encode()


def _derive(purpose: str) -> bytes:
    return hmac.new(master_key(), purpose.encode(), hashlib.sha256).digest()


def password_for(slug: str) -> str:
    """Deterministic password for a client slug (or INTERNAL), e.g. 'K3QX-7MPL-...'."""
    text = base64.b32encode(_derive(f"password:{slug}")).decode()[:20]
    return "-".join(text[i : i + 4] for i in range(0, 20, 4))


def fernet(purpose: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(_derive(f"fernet:{purpose}")))


def encrypt_xlsx(plain: bytes, password: str) -> bytes:
    out = io.BytesIO()
    OOXMLFile(io.BytesIO(plain)).encrypt(password, out)
    return out.getvalue()


def decrypt_xlsx(encrypted: bytes, password: str) -> bytes:
    office = msoffcrypto.OfficeFile(io.BytesIO(encrypted))
    office.load_key(password=password)
    out = io.BytesIO()
    office.decrypt(out)
    return out.getvalue()


def write_encrypted_zip(path: Path, files: dict[str, bytes], password: str) -> None:
    with pyzipper.AESZipFile(path, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as archive:
        archive.setpassword(password.encode())
        for name, data in files.items():
            archive.writestr(name, data)


def read_encrypted_zip(path: Path, password: str) -> dict[str, bytes]:
    with pyzipper.AESZipFile(path) as archive:
        archive.setpassword(password.encode())
        return {name: archive.read(name) for name in archive.namelist()}
