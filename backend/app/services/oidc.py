from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlparse
from uuid import uuid4

import httpx
from jose import JWTError, jwt

from app.core.config import get_settings
from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.database import decode_json, encode_json, get_connection
from app.models.schemas import OIDCAuthorizationResponse, OIDCProviderCreateRequest, OIDCProviderRecord


class OIDCService:
    def list_providers(self, organization_id: str) -> list[OIDCProviderRecord]:
        with get_connection() as connection:
            rows = connection.execute(
                "SELECT * FROM oidc_providers WHERE organization_id = ? ORDER BY name",
                (organization_id,),
            ).fetchall()
        return [self._row_to_provider(row) for row in rows]

    def create_provider(
        self,
        organization_id: str,
        created_by: str,
        payload: OIDCProviderCreateRequest,
    ) -> OIDCProviderRecord:
        issuer_url = payload.issuer_url.rstrip("/")
        self._validate_issuer(issuer_url)
        if "openid" not in payload.scopes:
            raise ValueError("OIDC scopes must include openid.")
        provider_id = f"oidc_{uuid4().hex}"
        now = datetime.now(timezone.utc).isoformat()
        try:
            with get_connection() as connection:
                connection.execute(
                    """
                    INSERT INTO oidc_providers (
                        provider_id, organization_id, name, issuer_url, client_id,
                        client_secret_cipher, scopes_json, enabled, created_by,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        provider_id,
                        organization_id,
                        payload.name,
                        issuer_url,
                        payload.client_id,
                        encrypt_secret(payload.client_secret),
                        encode_json(payload.scopes),
                        payload.enabled,
                        created_by,
                        now,
                        now,
                    ),
                )
        except Exception as exc:
            if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                raise ValueError("This OIDC issuer is already configured.") from exc
            raise
        return self.get_provider(provider_id, organization_id, include_secret=False)

    def get_provider(
        self, provider_id: str, organization_id: str | None = None, *, include_secret: bool = False
    ):
        where = "provider_id = ?"
        params: tuple[object, ...] = (provider_id,)
        if organization_id:
            where += " AND organization_id = ?"
            params += (organization_id,)
        with get_connection() as connection:
            row = connection.execute(
                f"SELECT * FROM oidc_providers WHERE {where}", params
            ).fetchone()
        if row is None:
            raise ValueError("OIDC provider not found.")
        if include_secret:
            return row
        return self._row_to_provider(row)

    async def start_authorization(self, provider_id: str) -> OIDCAuthorizationResponse:
        provider = self.get_provider(provider_id, include_secret=True)
        if not bool(provider["enabled"]):
            raise ValueError("OIDC provider is disabled.")
        discovery = await self._discovery(provider["issuer_url"])
        authorization_endpoint = discovery.get("authorization_endpoint")
        if not isinstance(authorization_endpoint, str):
            raise ValueError("OIDC discovery is missing authorization_endpoint.")
        state = secrets.token_urlsafe(36)
        nonce = secrets.token_urlsafe(36)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).decode("ascii").rstrip("=")
        redirect_uri = self._redirect_uri(provider_id)
        now = datetime.now(timezone.utc)
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO oidc_authorization_states (
                    state_hash, provider_id, organization_id, nonce,
                    code_verifier_cipher, redirect_uri, expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._hash(state),
                    provider_id,
                    provider["organization_id"],
                    nonce,
                    encrypt_secret(verifier),
                    redirect_uri,
                    (now + timedelta(minutes=10)).isoformat(),
                    now.isoformat(),
                ),
            )
        query = urlencode(
            {
                "response_type": "code",
                "client_id": provider["client_id"],
                "redirect_uri": redirect_uri,
                "scope": " ".join(decode_json(provider["scopes_json"], ["openid", "email"])),
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return OIDCAuthorizationResponse(authorization_url=f"{authorization_endpoint}?{query}")

    async def complete_authorization(self, provider_id: str, code: str, state: str) -> tuple[str, str]:
        state_hash = self._hash(state)
        with get_connection() as connection:
            saved_state = connection.execute(
                "SELECT * FROM oidc_authorization_states WHERE state_hash = ? AND provider_id = ?",
                (state_hash, provider_id),
            ).fetchone()
            if saved_state is not None:
                connection.execute(
                    "DELETE FROM oidc_authorization_states WHERE state_hash = ?",
                    (state_hash,),
                )
        if saved_state is None:
            raise ValueError("OIDC state is invalid or already used.")
        expires_at = datetime.fromisoformat(str(saved_state["expires_at"]).replace("Z", "+00:00"))
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            raise ValueError("OIDC state has expired.")
        provider = self.get_provider(provider_id, saved_state["organization_id"], include_secret=True)
        discovery = await self._discovery(provider["issuer_url"])
        token_endpoint = discovery.get("token_endpoint")
        jwks_uri = discovery.get("jwks_uri")
        if not isinstance(token_endpoint, str) or not isinstance(jwks_uri, str):
            raise ValueError("OIDC discovery is incomplete.")
        async with httpx.AsyncClient(timeout=15.0) as client:
            token_response = await client.post(
                token_endpoint,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": provider["client_id"],
                    "client_secret": decrypt_secret(provider["client_secret_cipher"]),
                    "redirect_uri": saved_state["redirect_uri"],
                    "code_verifier": decrypt_secret(saved_state["code_verifier_cipher"]),
                },
                headers={"Accept": "application/json"},
            )
            token_response.raise_for_status()
            token_payload = token_response.json()
            jwks_response = await client.get(jwks_uri, headers={"Accept": "application/json"})
            jwks_response.raise_for_status()
            jwks = jwks_response.json()
        id_token = token_payload.get("id_token")
        if not isinstance(id_token, str):
            raise ValueError("OIDC token response did not contain an ID token.")
        header = jwt.get_unverified_header(id_token)
        algorithm = header.get("alg")
        if algorithm not in {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512"}:
            raise ValueError("OIDC ID token uses an unsupported signing algorithm.")
        key = next((item for item in jwks.get("keys", []) if item.get("kid") == header.get("kid")), None)
        if key is None:
            raise ValueError("OIDC signing key was not found.")
        try:
            claims = jwt.decode(
                id_token,
                key,
                algorithms=[algorithm],
                audience=provider["client_id"],
                issuer=provider["issuer_url"],
                options={"require_exp": True, "require_iat": True},
            )
        except JWTError as exc:
            raise ValueError("OIDC ID token validation failed.") from exc
        if not secrets.compare_digest(str(claims.get("nonce", "")), saved_state["nonce"]):
            raise ValueError("OIDC nonce validation failed.")
        email = claims.get("email")
        if not isinstance(email, str) or claims.get("email_verified") is False:
            raise ValueError("OIDC provider did not return a verified email address.")
        return email.lower(), saved_state["organization_id"]

    async def _discovery(self, issuer_url: str) -> dict[str, object]:
        self._validate_issuer(issuer_url)
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{issuer_url.rstrip('/')}/.well-known/openid-configuration",
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            discovery = response.json()
        if discovery.get("issuer", "").rstrip("/") != issuer_url.rstrip("/"):
            raise ValueError("OIDC discovery issuer does not match configuration.")
        return discovery

    @staticmethod
    def _validate_issuer(issuer_url: str) -> None:
        parsed = urlparse(issuer_url)
        local = parsed.hostname in {"127.0.0.1", "localhost"}
        if not parsed.hostname or parsed.scheme not in ({"https"} if not local else {"http", "https"}):
            raise ValueError("OIDC issuer must use HTTPS (localhost may use HTTP).")
        if parsed.username or parsed.password or parsed.fragment:
            raise ValueError("OIDC issuer URL is not valid.")

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _redirect_uri(provider_id: str) -> str:
        return f"{get_settings().oidc_redirect_base_url.rstrip('/')}/{provider_id}/callback"

    @staticmethod
    def _row_to_provider(row) -> OIDCProviderRecord:
        return OIDCProviderRecord(
            provider_id=row["provider_id"],
            organization_id=row["organization_id"],
            name=row["name"],
            issuer_url=row["issuer_url"],
            client_id=row["client_id"],
            scopes=decode_json(row["scopes_json"], []),
            enabled=bool(row["enabled"]),
            created_at=str(row["created_at"]) if row["created_at"] is not None else None,
            updated_at=str(row["updated_at"]) if row["updated_at"] is not None else None,
        )


oidc_service = OIDCService()
