import hashlib
import secrets

from app.core.database import decode_json, encode_json, get_connection, is_postgres_database
from app.models.schemas import UserContext

DEMO_PASSWORD = "demo-password"

DEMO_USERS = [
    UserContext(
        user_id="u_admin",
        email="admin@demo.local",
        role="admin",
        scopes=["documents:read", "documents:write", "tasks:write", "email:send", "audit:read"],
    ),
    UserContext(
        user_id="u_manager",
        email="manager@demo.local",
        role="manager",
        scopes=["documents:read", "documents:write", "tasks:write", "audit:read"],
    ),
    UserContext(
        user_id="u_employee",
        email="employee@demo.local",
        role="employee",
        scopes=["documents:read", "documents:write"],
    ),
]


def _hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 120_000)
    return f"pbkdf2_sha256$120000${salt}${digest.hex()}"


def _verify_password(password: str, stored_hash: str) -> bool:
    algorithm, iterations, salt, expected = stored_hash.split("$")
    if algorithm != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        int(iterations),
    )
    return secrets.compare_digest(digest.hex(), expected)


class UserService:
    def seed_demo_users(self) -> None:
        if is_postgres_database():
            insert_sql = """
                INSERT INTO users
                    (user_id, email, password_hash, role, scopes_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (user_id) DO NOTHING
            """
        else:
            insert_sql = """
                INSERT OR IGNORE INTO users
                    (user_id, email, password_hash, role, scopes_json)
                VALUES (?, ?, ?, ?, ?)
            """

        with get_connection() as connection:
            for user in DEMO_USERS:
                connection.execute(
                    insert_sql,
                    (
                        user.user_id,
                        user.email,
                        _hash_password(DEMO_PASSWORD),
                        user.role,
                        encode_json(user.scopes),
                    ),
                )

    def authenticate(self, email: str, password: str) -> UserContext | None:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE lower(email) = lower(?)",
                (email,),
            ).fetchone()
        if row is None or not _verify_password(password, row["password_hash"]):
            return None
        return UserContext(
            user_id=row["user_id"],
            email=row["email"],
            role=row["role"],
            scopes=decode_json(row["scopes_json"], []),
        )


user_service = UserService()
