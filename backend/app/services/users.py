from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.core.database import decode_json, encode_json, get_connection, is_postgres_database
from app.models.schemas import (
    InvitationCreateRequest,
    InvitationRecord,
    MembershipUpdateRequest,
    OrganizationMemberRecord,
    OrganizationSummary,
    UserContext,
)

DEMO_PASSWORD = "demo-password"
DEFAULT_ORGANIZATION_ID = "org_default"
DEFAULT_ORGANIZATION_SLUG = "default"
ALL_SCOPES = {
    "documents:read",
    "documents:write",
    "tasks:write",
    "email:send",
    "data:export",
    "audit:read",
    "organization:manage",
    "oidc:manage",
}
ROLE_DEFAULT_SCOPES = {
    "admin": sorted(ALL_SCOPES),
    "manager": ["audit:read", "documents:read", "documents:write", "tasks:write"],
    "employee": ["documents:read", "documents:write"],
}

DEMO_USERS = (
    ("u_admin", "admin@demo.local", "Demo Admin", "admin"),
    ("u_manager", "manager@demo.local", "Demo Manager", "manager"),
    ("u_employee", "employee@demo.local", "Demo Employee", "employee"),
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=timezone.utc)


def _hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash_password(password: str, salt: str | None = None, iterations: int = 310_000) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations
    )
    return f"pbkdf2_sha256${iterations}${salt}${digest.hex()}"


def _verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations, salt, expected = stored_hash.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations)
        )
        return secrets.compare_digest(digest.hex(), expected)
    except (TypeError, ValueError):
        return False


class UserService:
    def seed_demo_users(self) -> None:
        user_insert = (
            """
            INSERT INTO users
                (user_id, email, password_hash, role, scopes_json, display_name)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (user_id) DO NOTHING
            """
            if is_postgres_database()
            else """
            INSERT OR IGNORE INTO users
                (user_id, email, password_hash, role, scopes_json, display_name)
            VALUES (?, ?, ?, ?, ?, ?)
            """
        )
        membership_insert = (
            """
            INSERT INTO organization_memberships
                (membership_id, organization_id, user_id, role, scopes_json, status)
            VALUES (?, ?, ?, ?, ?, 'active')
            ON CONFLICT (organization_id, user_id) DO NOTHING
            """
            if is_postgres_database()
            else """
            INSERT OR IGNORE INTO organization_memberships
                (membership_id, organization_id, user_id, role, scopes_json, status)
            VALUES (?, ?, ?, ?, ?, 'active')
            """
        )
        with get_connection() as connection:
            for user_id, email, display_name, role in DEMO_USERS:
                scopes = ROLE_DEFAULT_SCOPES[role]
                connection.execute(
                    user_insert,
                    (
                        user_id,
                        email,
                        _hash_password(DEMO_PASSWORD),
                        role,
                        encode_json(scopes),
                        display_name,
                    ),
                )
                connection.execute(
                    membership_insert,
                    (
                        f"mem_default_{user_id}",
                        DEFAULT_ORGANIZATION_ID,
                        user_id,
                        role,
                        encode_json(scopes),
                    ),
                )

    def authenticate(
        self, email: str, password: str, organization_slug: str | None = None
    ) -> UserContext | None:
        params: list[object] = [email]
        slug_filter = ""
        if organization_slug:
            slug_filter = "AND lower(o.slug) = lower(?)"
            params.append(organization_slug)
        with get_connection() as connection:
            row = connection.execute(
                f"""
                SELECT u.*, m.membership_id, m.organization_id,
                       m.role AS membership_role, m.scopes_json AS membership_scopes_json,
                       o.slug AS organization_slug, o.name AS organization_name
                FROM users u
                JOIN organization_memberships m ON m.user_id = u.user_id
                JOIN organizations o ON o.organization_id = m.organization_id
                WHERE lower(u.email) = lower(?)
                  AND u.disabled = ?
                  AND m.status = 'active'
                  {slug_filter}
                ORDER BY CASE WHEN o.organization_id = ? THEN 0 ELSE 1 END, o.created_at
                LIMIT 1
                """,
                (*params, False, DEFAULT_ORGANIZATION_ID),
            ).fetchone()
            if row is None or not _verify_password(password, row["password_hash"]):
                return None
            now = _now().isoformat()
            connection.execute(
                "UPDATE users SET last_login_at = ? WHERE user_id = ?",
                (now, row["user_id"]),
            )
        return self._row_to_user(row)

    def get_by_id(
        self, user_id: str, organization_id: str = DEFAULT_ORGANIZATION_ID
    ) -> UserContext | None:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT u.*, m.membership_id, m.organization_id,
                       m.role AS membership_role, m.scopes_json AS membership_scopes_json,
                       o.slug AS organization_slug, o.name AS organization_name
                FROM users u
                JOIN organization_memberships m ON m.user_id = u.user_id
                JOIN organizations o ON o.organization_id = m.organization_id
                WHERE u.user_id = ? AND m.organization_id = ?
                  AND u.disabled = ? AND m.status = 'active'
                """,
                (user_id, organization_id, False),
            ).fetchone()
        return self._row_to_user(row) if row is not None else None

    def get_by_email(self, email: str, organization_id: str) -> UserContext | None:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT u.*, m.membership_id, m.organization_id,
                       m.role AS membership_role, m.scopes_json AS membership_scopes_json,
                       o.slug AS organization_slug, o.name AS organization_name
                FROM users u
                JOIN organization_memberships m ON m.user_id = u.user_id
                JOIN organizations o ON o.organization_id = m.organization_id
                WHERE lower(u.email) = lower(?) AND m.organization_id = ?
                  AND u.disabled = ? AND m.status = 'active'
                """,
                (email, organization_id, False),
            ).fetchone()
        return self._row_to_user(row) if row is not None else None

    def get_session_user(
        self, session_id: str, user_id: str, organization_id: str, token_version: int
    ) -> UserContext | None:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT u.*, m.membership_id, m.organization_id,
                       m.role AS membership_role, m.scopes_json AS membership_scopes_json,
                       o.slug AS organization_slug, o.name AS organization_name,
                       s.expires_at, s.revoked_at
                FROM auth_sessions s
                JOIN users u ON u.user_id = s.user_id
                JOIN organization_memberships m ON m.membership_id = s.membership_id
                JOIN organizations o ON o.organization_id = s.organization_id
                WHERE s.session_id = ? AND s.user_id = ? AND s.organization_id = ?
                  AND m.organization_id = s.organization_id
                  AND u.disabled = ? AND m.status = 'active'
                """,
                (session_id, user_id, organization_id, False),
            ).fetchone()
        if row is None or row["revoked_at"] is not None:
            return None
        if int(row["token_version"]) != int(token_version):
            return None
        if _as_datetime(row["expires_at"]) <= _now():
            return None
        return self._row_to_user(row)

    def create_session(
        self, user: UserContext, refresh_token_days: int
    ) -> tuple[str, str]:
        session_id = f"ses_{uuid4().hex}"
        refresh_token = secrets.token_urlsafe(48)
        now = _now()
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO auth_sessions (
                    session_id, user_id, organization_id, membership_id,
                    refresh_token_hash, expires_at, created_at, last_used_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    user.user_id,
                    user.organization_id,
                    user.membership_id,
                    _hash_secret(refresh_token),
                    (now + timedelta(days=refresh_token_days)).isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
        return session_id, refresh_token

    def rotate_session(
        self, refresh_token: str, refresh_token_days: int
    ) -> tuple[UserContext, str, str] | None:
        token_hash = _hash_secret(refresh_token)
        now = _now()
        with get_connection() as connection:
            row = connection.execute(
                "SELECT * FROM auth_sessions WHERE refresh_token_hash = ?",
                (token_hash,),
            ).fetchone()
            if (
                row is None
                or row["revoked_at"] is not None
                or _as_datetime(row["expires_at"]) <= now
            ):
                return None
            cursor = connection.execute(
                "UPDATE auth_sessions SET revoked_at = ?, last_used_at = ? WHERE session_id = ? AND revoked_at IS NULL",
                (now.isoformat(), now.isoformat(), row["session_id"]),
            )
            if cursor.rowcount != 1:
                return None
        user = self.get_by_id(row["user_id"], row["organization_id"])
        if user is None:
            return None
        session_id, new_refresh_token = self.create_session(user, refresh_token_days)
        return user, session_id, new_refresh_token

    def revoke_session(self, session_id: str, user_id: str) -> bool:
        with get_connection() as connection:
            cursor = connection.execute(
                """
                UPDATE auth_sessions SET revoked_at = ?
                WHERE session_id = ? AND user_id = ? AND revoked_at IS NULL
                """,
                (_now().isoformat(), session_id, user_id),
            )
            return cursor.rowcount > 0

    def list_organizations(self, user_id: str) -> list[OrganizationSummary]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT o.organization_id, o.slug, o.name, m.membership_id,
                       m.role, m.scopes_json, m.status
                FROM organization_memberships m
                JOIN organizations o ON o.organization_id = m.organization_id
                WHERE m.user_id = ?
                ORDER BY o.name
                """,
                (user_id,),
            ).fetchall()
        return [
            OrganizationSummary(
                organization_id=row["organization_id"],
                slug=row["slug"],
                name=row["name"],
                membership_id=row["membership_id"],
                role=row["role"],
                scopes=decode_json(row["scopes_json"], []),
                status=row["status"],
            )
            for row in rows
        ]

    def create_organization(self, name: str, slug: str, creator: UserContext) -> OrganizationSummary:
        organization_id = f"org_{uuid4().hex}"
        membership_id = f"mem_{uuid4().hex}"
        now = _now().isoformat()
        with get_connection() as connection:
            if connection.execute(
                "SELECT 1 FROM organizations WHERE lower(slug) = lower(?)", (slug,)
            ).fetchone():
                raise ValueError("Organization slug is already in use.")
            connection.execute(
                """
                INSERT INTO organizations (organization_id, slug, name, created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (organization_id, slug, name, creator.user_id, now, now),
            )
            connection.execute(
                """
                INSERT INTO organization_memberships (
                    membership_id, organization_id, user_id, role, scopes_json,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, 'admin', ?, 'active', ?, ?)
                """,
                (
                    membership_id,
                    organization_id,
                    creator.user_id,
                    encode_json(ROLE_DEFAULT_SCOPES["admin"]),
                    now,
                    now,
                ),
            )
        return OrganizationSummary(
            organization_id=organization_id,
            slug=slug,
            name=name,
            membership_id=membership_id,
            role="admin",
            scopes=ROLE_DEFAULT_SCOPES["admin"],
            status="active",
        )

    def list_members(self, organization_id: str) -> list[OrganizationMemberRecord]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT m.*, u.email, u.display_name
                FROM organization_memberships m
                JOIN users u ON u.user_id = m.user_id
                WHERE m.organization_id = ?
                ORDER BY u.email
                """,
                (organization_id,),
            ).fetchall()
        return [self._row_to_member(row) for row in rows]

    def update_membership(
        self,
        organization_id: str,
        membership_id: str,
        payload: MembershipUpdateRequest,
        actor: UserContext,
    ) -> OrganizationMemberRecord | None:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT * FROM organization_memberships WHERE membership_id = ? AND organization_id = ?",
                (membership_id, organization_id),
            ).fetchone()
            if row is None:
                return None
            new_role = payload.role or row["role"]
            new_status = payload.status or row["status"]
            if row["role"] == "admin" and (new_role != "admin" or new_status != "active"):
                active_admins = connection.execute(
                    "SELECT COUNT(*) AS total FROM organization_memberships WHERE organization_id = ? AND role = 'admin' AND status = 'active'",
                    (organization_id,),
                ).fetchone()["total"]
                if int(active_admins) <= 1:
                    raise ValueError("An organization must retain at least one active admin.")
            if row["user_id"] == actor.user_id and new_status != "active":
                raise ValueError("You cannot suspend your own active membership.")
            scopes = payload.scopes if payload.scopes is not None else decode_json(row["scopes_json"], [])
            self._validate_scopes(scopes)
            connection.execute(
                """
                UPDATE organization_memberships
                SET role = ?, scopes_json = ?, status = ?, updated_at = ?
                WHERE membership_id = ? AND organization_id = ?
                """,
                (
                    new_role,
                    encode_json(sorted(set(scopes))),
                    new_status,
                    _now().isoformat(),
                    membership_id,
                    organization_id,
                ),
            )
            updated = connection.execute(
                """
                SELECT m.*, u.email, u.display_name
                FROM organization_memberships m JOIN users u ON u.user_id = m.user_id
                WHERE m.membership_id = ? AND m.organization_id = ?
                """,
                (membership_id, organization_id),
            ).fetchone()
        return self._row_to_member(updated)

    def create_invitation(
        self, organization_id: str, payload: InvitationCreateRequest, invited_by: str
    ) -> InvitationRecord:
        scopes = payload.scopes or ROLE_DEFAULT_SCOPES[payload.role]
        self._validate_scopes(scopes)
        email = payload.email.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise ValueError("Enter a valid email address.")
        token = secrets.token_urlsafe(48)
        invitation_id = f"inv_{uuid4().hex}"
        now = _now()
        expires_at = now + timedelta(hours=payload.expires_in_hours)
        with get_connection() as connection:
            if connection.execute(
                """
                SELECT 1 FROM organization_memberships m JOIN users u ON u.user_id = m.user_id
                WHERE m.organization_id = ? AND lower(u.email) = lower(?)
                """,
                (organization_id, email),
            ).fetchone():
                raise ValueError("This user is already a member of the organization.")
            connection.execute(
                "UPDATE organization_invitations SET status = 'revoked' WHERE organization_id = ? AND lower(email) = lower(?) AND status = 'pending'",
                (organization_id, email),
            )
            connection.execute(
                """
                INSERT INTO organization_invitations (
                    invitation_id, organization_id, email, role, scopes_json,
                    token_hash, status, invited_by, expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    invitation_id,
                    organization_id,
                    email,
                    payload.role,
                    encode_json(sorted(set(scopes))),
                    _hash_secret(token),
                    invited_by,
                    expires_at.isoformat(),
                    now.isoformat(),
                ),
            )
        return InvitationRecord(
            invitation_id=invitation_id,
            organization_id=organization_id,
            email=email,
            role=payload.role,
            scopes=sorted(set(scopes)),
            status="pending",
            invited_by=invited_by,
            expires_at=expires_at.isoformat(),
            created_at=now.isoformat(),
            invitation_token=token,
        )

    def list_invitations(self, organization_id: str) -> list[InvitationRecord]:
        now = _now()
        with get_connection() as connection:
            connection.execute(
                "UPDATE organization_invitations SET status = 'expired' WHERE organization_id = ? AND status = 'pending' AND expires_at < ?",
                (organization_id, now.isoformat()),
            )
            rows = connection.execute(
                "SELECT * FROM organization_invitations WHERE organization_id = ? ORDER BY created_at DESC",
                (organization_id,),
            ).fetchall()
        return [self._row_to_invitation(row) for row in rows]

    def accept_invitation(
        self, token: str, display_name: str, password: str
    ) -> UserContext:
        now = _now()
        token_hash = _hash_secret(token)
        with get_connection() as connection:
            invitation = connection.execute(
                "SELECT * FROM organization_invitations WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
            if invitation is None or invitation["status"] != "pending":
                raise ValueError("Invitation is invalid or has already been used.")
            if _as_datetime(invitation["expires_at"]) <= now:
                connection.execute(
                    "UPDATE organization_invitations SET status = 'expired' WHERE invitation_id = ?",
                    (invitation["invitation_id"],),
                )
                raise ValueError("Invitation has expired.")
            user = connection.execute(
                "SELECT * FROM users WHERE lower(email) = lower(?)",
                (invitation["email"],),
            ).fetchone()
            if user is None:
                user_id = f"usr_{uuid4().hex}"
                connection.execute(
                    """
                    INSERT INTO users (
                        user_id, email, password_hash, role, scopes_json,
                        display_name, password_changed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        invitation["email"],
                        _hash_password(password),
                        invitation["role"],
                        invitation["scopes_json"],
                        display_name.strip(),
                        now.isoformat(),
                    ),
                )
            else:
                if not _verify_password(password, user["password_hash"]):
                    raise PermissionError("Existing users must enter their current password.")
                if bool(user["disabled"]):
                    raise PermissionError("This user account is disabled.")
                user_id = user["user_id"]
            membership_id = f"mem_{uuid4().hex}"
            connection.execute(
                """
                INSERT INTO organization_memberships (
                    membership_id, organization_id, user_id, role, scopes_json,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    membership_id,
                    invitation["organization_id"],
                    user_id,
                    invitation["role"],
                    invitation["scopes_json"],
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            connection.execute(
                """
                UPDATE organization_invitations
                SET status = 'accepted', accepted_by = ?, accepted_at = ?
                WHERE invitation_id = ? AND status = 'pending'
                """,
                (user_id, now.isoformat(), invitation["invitation_id"]),
            )
        accepted = self.get_by_id(user_id, invitation["organization_id"])
        if accepted is None:
            raise ValueError("Could not activate the invited membership.")
        return accepted

    @staticmethod
    def _validate_scopes(scopes: list[str]) -> None:
        unknown = sorted(set(scopes) - ALL_SCOPES)
        if unknown:
            raise ValueError(f"Unknown scopes: {', '.join(unknown)}")

    @staticmethod
    def _row_to_user(row) -> UserContext:
        return UserContext(
            user_id=row["user_id"],
            email=row["email"],
            display_name=row["display_name"] or "",
            organization_id=row["organization_id"],
            organization_slug=row["organization_slug"],
            organization_name=row["organization_name"],
            membership_id=row["membership_id"],
            role=row["membership_role"],
            scopes=decode_json(row["membership_scopes_json"], []),
        )

    @staticmethod
    def _row_to_member(row) -> OrganizationMemberRecord:
        return OrganizationMemberRecord(
            membership_id=row["membership_id"],
            organization_id=row["organization_id"],
            user_id=row["user_id"],
            email=row["email"],
            display_name=row["display_name"] or "",
            role=row["role"],
            scopes=decode_json(row["scopes_json"], []),
            status=row["status"],
            created_at=str(row["created_at"]) if row["created_at"] is not None else None,
        )

    @staticmethod
    def _row_to_invitation(row) -> InvitationRecord:
        return InvitationRecord(
            invitation_id=row["invitation_id"],
            organization_id=row["organization_id"],
            email=row["email"],
            role=row["role"],
            scopes=decode_json(row["scopes_json"], []),
            status=row["status"],
            invited_by=row["invited_by"],
            expires_at=str(row["expires_at"]),
            created_at=str(row["created_at"]) if row["created_at"] is not None else None,
        )


user_service = UserService()
