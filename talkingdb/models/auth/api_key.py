import secrets
from datetime import datetime, timezone
from typing import Optional, Type
import sqlite3

class APIKeyModel:
    def __init__(
        self,
        user_email: str,
        api_key: str,
        created_at: datetime,
    ):
        self.user_email = user_email
        self.api_key = api_key
        self.created_at = created_at

    
    @staticmethod
    def init_db(conn: sqlite3.Connection) -> None:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            api_key TEXT PRIMARY KEY,
            user_email TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """)

    
    @staticmethod
    def generate_key() -> str:
        return "sk_" + secrets.token_urlsafe(32)

   
    @classmethod
    def create(
        cls: Type["APIKeyModel"],
        conn: sqlite3.Connection,
        user_email: str,
    ) -> "APIKeyModel":

        api_key = cls.generate_key()
        created_at = datetime.now(timezone.utc)

        conn.execute(
            """
            INSERT INTO api_keys (
                api_key,
                user_email,
                created_at
            )
            VALUES (?, ?, ?)
            """,
            (
                api_key,
                user_email,
                created_at.isoformat(),
            ),
        )

        conn.commit()

        return cls(
            user_email=user_email,
            api_key=api_key,
            created_at=created_at,
        )

    @classmethod
    def verify(
        cls,
        conn: sqlite3.Connection,
        api_key: str,
    ) -> Optional[str]:

        row = conn.execute(
            """
            SELECT user_email
            FROM api_keys
            WHERE api_key = ?
            """,
            (api_key,),
        ).fetchone()

        if not row:
            return None

        return row[0]