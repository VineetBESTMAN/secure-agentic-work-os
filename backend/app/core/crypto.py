import base64
import hashlib
import json

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


PREFIX = "enc:v1"


def _fernet(secret: str) -> Fernet:
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _keyring() -> dict[str, str]:
    settings = get_settings()
    if not settings.encryption_keyring:
        return {"legacy": settings.secret_key}
    try:
        parsed = json.loads(settings.encryption_keyring)
    except json.JSONDecodeError as exc:
        raise ValueError("APP_ENCRYPTION_KEYRING must be a JSON object.") from exc
    if not isinstance(parsed, dict) or not parsed:
        raise ValueError("APP_ENCRYPTION_KEYRING must contain at least one key.")
    keyring = {
        str(key_id): str(secret)
        for key_id, secret in parsed.items()
        if str(key_id).strip() and str(secret)
    }
    if len(keyring) != len(parsed):
        raise ValueError("Encryption key IDs and values must be non-empty.")
    return keyring


def active_encryption_key_id() -> str:
    settings = get_settings()
    keys = _keyring()
    if settings.encryption_keyring:
        if settings.active_encryption_key_id not in keys:
            raise ValueError("APP_ACTIVE_ENCRYPTION_KEY_ID is absent from the keyring.")
        return settings.active_encryption_key_id
    return "legacy"


def encryption_key_ids() -> list[str]:
    return sorted(_keyring())


def encrypt_secret(value: str | None) -> str | None:
    if not value:
        return None
    key_id = active_encryption_key_id()
    token = _fernet(_keyring()[key_id]).encrypt(value.encode("utf-8")).decode("utf-8")
    return f"{PREFIX}:{key_id}:{token}"


def decrypt_secret(value: str | None) -> str | None:
    if not value:
        return None
    keys = _keyring()
    if value.startswith(f"{PREFIX}:"):
        try:
            _, _, key_id, token = value.split(":", 3)
        except ValueError as exc:
            raise ValueError("Encrypted secret envelope is malformed.") from exc
        secret = keys.get(key_id)
        if secret is None:
            raise ValueError(f"Encryption key '{key_id}' is unavailable.")
        try:
            return _fernet(secret).decrypt(token.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("Encrypted secret authentication failed.") from exc

    candidates = [get_settings().secret_key, *keys.values()]
    for secret in dict.fromkeys(candidates):
        try:
            return _fernet(secret).decrypt(value.encode("utf-8")).decode("utf-8")
        except InvalidToken:
            continue
    raise ValueError("Encrypted secret could not be decrypted with the configured keyring.")


def encrypted_key_id(value: str | None) -> str:
    if not value:
        return "empty"
    if value.startswith(f"{PREFIX}:"):
        parts = value.split(":", 3)
        return parts[2] if len(parts) == 4 else "malformed"
    return "legacy"


def reencrypt_secret(value: str | None) -> str | None:
    if not value:
        return value
    return encrypt_secret(decrypt_secret(value))
