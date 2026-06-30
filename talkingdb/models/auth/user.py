import sqlite3
from datetime import datetime, timezone
from typing import Optional, Type
from pydantic import BaseModel, EmailStr, Field

class UserModel(BaseModel):
    email: EmailStr
    password_hash: str

    is_verified: bool = False
    is_active: bool = True

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @staticmethod
    def init_db(conn: sqlite3.Connection) -> None:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            email TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            is_verified INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """)

        conn.commit()

    @classmethod
    def create(
        cls: Type["UserModel"],
        conn: sqlite3.Connection,
        email: str,
        password_hash: str,
    ) -> "UserModel":

        existing = cls.find_by_email(conn, email)

        if existing:
            print("existing")
            print(existing)
            raise ValueError(
                "An account already exists with this email"
            )

        user = cls(
            email=email.lower(),
            password_hash=password_hash,
        )

        user.save(conn)

        return user

    @classmethod
    def find_by_email(
        cls: Type["UserModel"],
        conn: sqlite3.Connection,
        email: str,
    ) -> Optional["UserModel"]:

        cur = conn.cursor()

        row = cur.execute(
            """
            SELECT
                email,
                password_hash,
                is_verified,
                is_active,
                created_at,
                updated_at
            FROM users
            WHERE email = ?
            """,
            (email.lower(),)
        ).fetchone()

        if not row:
            return None

        return cls(
            email=row[0],
            password_hash=row[1],
            is_verified=bool(row[2]),
            is_active=bool(row[3]),
            created_at=datetime.fromisoformat(row[4]),
            updated_at=datetime.fromisoformat(row[5]),
        )

    def save(self, conn: sqlite3.Connection) -> None:
        self.updated_at = datetime.utcnow()

        conn.execute(
            """
            INSERT OR REPLACE INTO users (
                email,
                password_hash,
                is_verified,
                is_active,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                self.email.lower(),
                self.password_hash,
                int(self.is_verified),
                int(self.is_active),
                self.created_at.isoformat(),
                self.updated_at.isoformat(),
            )
        )

        conn.commit()

    def delete(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            "DELETE FROM users WHERE email = ?",
            (self.email.lower(),)
        )

        conn.commit()