"""
Password hashing utilities for the SCET Lab Manager admin login.

All new and migrated admin/staff passwords are stored as Argon2id hashes
(via argon2-cffi) in system_users.password_hash. A narrow legacy path lets
the app recognize old rows that still hold a plaintext value and verify
them one last time before immediately re-hashing them (see needs_rehash /
the lazy-migration call site in app.py's handle_admin_login).

Requires: pip install argon2-cffi
"""

import hmac

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerifyMismatchError

# Argon2id with library defaults (time_cost=3, memory_cost=64 MiB, parallelism=4).
_ph = PasswordHasher()


def hash_password(plain_password: str) -> str:
    """Return a new Argon2id hash string for the given plaintext password."""
    return _ph.hash(plain_password)


def is_argon2_hash(value) -> bool:
    """True if `value` looks like an Argon2 hash, not a legacy plaintext value."""
    return isinstance(value, str) and value.startswith("$argon2")


def _constant_time_eq(a: str, b: str) -> bool:
    try:
        return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
    except Exception:
        return False


def verify_password(plain_password: str, stored_value: str) -> bool:
    """
    Verify a plaintext password against whatever is currently stored in
    system_users.password_hash. Handles both already-migrated Argon2id
    hashes and legacy plaintext rows. Never raises.
    """
    if not stored_value or not plain_password:
        return False

    if is_argon2_hash(stored_value):
        try:
            return _ph.verify(stored_value, plain_password)
        except (VerifyMismatchError, InvalidHash):
            return False
        except Exception:
            return False

    # Legacy row: password_hash currently holds a plaintext value.
    return _constant_time_eq(plain_password, stored_value)


def needs_rehash(stored_value) -> bool:
    """
    True if this stored value should be replaced with a fresh Argon2id hash
    after the *next* successful login — either because it's still legacy
    plaintext, or because Argon2's parameters have since been tightened.
    Only call this after verify_password() has already returned True.
    """
    if not is_argon2_hash(stored_value):
        return True
    try:
        return _ph.check_needs_rehash(stored_value)
    except Exception:
        return True