"""Database access layer for InternAI.

Supports Oracle Database (Thin Mode via python-oracledb) with connection pooling
and parameterized bind variables. Includes automatic SQLite fallback for zero-setup
local development and testing.
"""

import logging
import os
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

import oracledb

# Ensure python-oracledb fetches CLOB as str and BLOB as bytes automatically
oracledb.defaults.fetch_lobs = False

logger = logging.getLogger(__name__)

# Global pool references
_oracle_pool: Optional[oracledb.ConnectionPool] = None
_active_backend: str = "oracle"
_sqlite_path: str = "internai.db"


def init_pool(app_config: Dict[str, Any]) -> None:
    """Initialize the database connection pool or fallback backend."""
    global _oracle_pool, _active_backend, _sqlite_path

    backend_pref = app_config.get("DB_BACKEND", "auto").lower()
    _sqlite_path = app_config.get("SQLITE_DB_PATH", "internai.db")

    if backend_pref == "sqlite":
        _active_backend = "sqlite"
        logger.info("Database backend explicitly configured to SQLite: %s", _sqlite_path)
        _init_sqlite_schema()
        return

    # Try connecting to Oracle
    user = app_config.get("ORACLE_USER")
    password = app_config.get("ORACLE_PASSWORD")
    dsn = app_config.get("ORACLE_DSN")
    min_pool = app_config.get("ORACLE_POOL_MIN", 2)
    max_pool = app_config.get("ORACLE_POOL_MAX", 10)
    inc_pool = app_config.get("ORACLE_POOL_INC", 1)

    try:
        logger.info("Attempting Oracle connection pool initialization (Thin mode) to %s...", dsn)
        _oracle_pool = oracledb.create_pool(
            user=user,
            password=password,
            dsn=dsn,
            min=min_pool,
            max=max_pool,
            increment=inc_pool,
        )
        # Test connection immediately
        test_conn = _oracle_pool.acquire()
        test_conn.ping()
        _oracle_pool.release(test_conn)
        
        _active_backend = "oracle"
        logger.info("Successfully connected to Oracle DB (Thin Mode).")
    except Exception as exc:
        if _oracle_pool is not None:
            try:
                _oracle_pool.close()
            except Exception:
                pass
            _oracle_pool = None

        if backend_pref == "oracle":
            logger.error("Failed to connect to Oracle database: %s", exc)
            raise RuntimeError(
                f"Oracle database connection failed. Verify ORACLE_DSN/USER/PASSWORD in .env: {exc}"
            ) from exc

        # Auto-fallback mode to SQLite
        logger.warning(
            "Oracle DB not accessible (%s). "
            "Falling back to local SQLite backend (%s) for seamless local execution.",
            exc,
            _sqlite_path,
        )
        _active_backend = "sqlite"
        _init_sqlite_schema()


def close_pool() -> None:
    """Close the Oracle connection pool if active."""
    global _oracle_pool
    if _oracle_pool is not None:
        try:
            _oracle_pool.close()
            logger.info("Oracle connection pool closed.")
        except Exception as exc:
            logger.warning("Error closing Oracle connection pool: %s", exc)
        _oracle_pool = None


def get_backend() -> str:
    """Return the active database backend ('oracle' or 'sqlite')."""
    return _active_backend


@contextmanager
def get_connection():
    """Context manager yielding a database connection with auto-rollback on error."""
    if _active_backend == "oracle":
        if _oracle_pool is None:
            raise RuntimeError("Oracle connection pool is not initialized.")
        conn = _oracle_pool.acquire()
        try:
            yield conn
        finally:
            _oracle_pool.release(conn)
    else:
        conn = sqlite3.connect(_sqlite_path)
        conn.row_factory = sqlite3.Row
        # Enable foreign keys in SQLite
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
        finally:
            conn.close()


def query_all(sql: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Execute a SELECT query and return all rows as a list of dictionaries.

    Uses bind variables exclusively.
    """
    bind_params = params or {}
    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(sql, bind_params)
            if _active_backend == "oracle":
                col_names = [d[0].lower() for d in cursor.description]
                rows = cursor.fetchall()
                return [dict(zip(col_names, row)) for row in rows]
            else:
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        finally:
            cursor.close()


def query_one(sql: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Execute a SELECT query and return a single row as a dictionary or None.

    Uses bind variables exclusively.
    """
    bind_params = params or {}
    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(sql, bind_params)
            if _active_backend == "oracle":
                row = cursor.fetchone()
                if row is None:
                    return None
                col_names = [d[0].lower() for d in cursor.description]
                return dict(zip(col_names, row))
            else:
                row = cursor.fetchone()
                if row is None:
                    return None
                return dict(row)
        finally:
            cursor.close()


def execute(sql: str, params: Optional[Dict[str, Any]] = None) -> int:
    """Execute an INSERT, UPDATE, or DELETE statement using bind variables.

    Commits the transaction and returns the affected row count.
    """
    bind_params = params or {}
    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(sql, bind_params)
            conn.commit()
            return cursor.rowcount
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()


def execute_returning_id(
    sql: str,
    params: Optional[Dict[str, Any]] = None,
    id_column: str = "id"
) -> int:
    """Execute an INSERT statement and return the generated ID.

    In Oracle: uses `RETURNING {id_column} INTO :out_id` with an oracledb bind variable.
    In SQLite: executes standard insert and returns cursor.lastrowid.
    """
    bind_params = dict(params or {})
    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            if _active_backend == "oracle":
                oracle_sql = sql.strip().rstrip(";")
                if "RETURNING" not in oracle_sql.upper():
                    oracle_sql = f"{oracle_sql} RETURNING {id_column} INTO :out_id"
                out_var = cursor.var(oracledb.NUMBER)
                bind_params["out_id"] = out_var
                cursor.execute(oracle_sql, bind_params)
                conn.commit()
                res = out_var.getvalue()
                return int(res[0]) if isinstance(res, list) else int(res)
            else:
                cursor.execute(sql, bind_params)
                conn.commit()
                return int(cursor.lastrowid)
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()


def execute_script(sql_script: str) -> None:
    """Execute multiple SQL statements sequentially."""
    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            if _active_backend == "oracle":
                # Split by '/' or ';'
                statements = [stmt.strip() for stmt in sql_script.split(";") if stmt.strip()]
                for stmt in statements:
                    cursor.execute(stmt)
                conn.commit()
            else:
                conn.executescript(sql_script)
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()


def _init_sqlite_schema() -> None:
    """Initialize local SQLite schema if using SQLite backend."""
    sqlite_schema = """
    CREATE TABLE IF NOT EXISTS USERS (
        user_id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        full_name TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'student' CHECK (role IN ('student', 'admin')),
        is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS STUDENT_PROFILES (
        profile_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE NOT NULL REFERENCES USERS(user_id) ON DELETE CASCADE,
        university TEXT,
        degree TEXT,
        graduation_year INTEGER,
        graduation_date DATE,
        gpa REAL,
        bio TEXT,
        cv_filename TEXT,
        cv_uploaded_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS SKILLS (
        skill_id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        category TEXT NOT NULL CHECK (category IN ('programming', 'web', 'data', 'cloud', 'soft_skills', 'tools')),
        description TEXT
    );

    CREATE TABLE IF NOT EXISTS STUDENT_SKILLS (
        student_skill_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES USERS(user_id) ON DELETE CASCADE,
        skill_id INTEGER NOT NULL REFERENCES SKILLS(skill_id) ON DELETE CASCADE,
        proficiency TEXT NOT NULL CHECK (proficiency IN ('Beginner', 'Intermediate', 'Advanced')),
        verified INTEGER NOT NULL DEFAULT 0 CHECK (verified IN (0, 1)),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (user_id, skill_id)
    );

    CREATE TABLE IF NOT EXISTS PROJECTS (
        project_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES USERS(user_id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        description TEXT,
        tech_used TEXT,
        project_url TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS JOBS (
        job_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER REFERENCES USERS(user_id) ON DELETE SET NULL,
        title TEXT NOT NULL,
        company TEXT,
        location TEXT,
        work_type TEXT NOT NULL DEFAULT 'onsite' CHECK (work_type IN ('onsite', 'remote', 'hybrid', 'unspecified')),
        duration TEXT,
        stipend TEXT,
        raw_text TEXT NOT NULL,
        role_summary TEXT,
        experience_required TEXT,
        education_required TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS JOB_REQUIREMENTS (
        req_id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL REFERENCES JOBS(job_id) ON DELETE CASCADE,
        skill_name TEXT NOT NULL,
        req_type TEXT NOT NULL DEFAULT 'required' CHECK (req_type IN ('required', 'preferred', 'other')),
        importance TEXT NOT NULL DEFAULT 'critical' CHECK (importance IN ('critical', 'nice_to_have', 'neutral'))
    );

    CREATE TABLE IF NOT EXISTS JOB_ANALYSIS (
        analysis_id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER UNIQUE NOT NULL REFERENCES JOBS(job_id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL REFERENCES USERS(user_id) ON DELETE CASCADE,
        structured_json TEXT,
        risk_score REAL,
        risk_level TEXT DEFAULT 'Safe' CHECK (risk_level IN ('Safe', 'Low', 'Medium', 'High')),
        risk_summary TEXT,
        match_percentage REAL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS APPLICATIONS (
        application_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES USERS(user_id) ON DELETE CASCADE,
        job_id INTEGER REFERENCES JOBS(job_id) ON DELETE SET NULL,
        company_name TEXT NOT NULL,
        role_title TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'Saved' CHECK (status IN ('Saved', 'Applied', 'Interview', 'Offer', 'Closed')),
        application_date DATE,
        deadline DATE,
        notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS CV_ANALYSIS (
        cv_analysis_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES USERS(user_id) ON DELETE CASCADE,
        job_id INTEGER REFERENCES JOBS(job_id) ON DELETE SET NULL,
        raw_cv_text TEXT,
        extracted_data TEXT,
        cv_score INTEGER,
        score_breakdown TEXT,
        suggestions TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS INTERVIEW_SESSIONS (
        session_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES USERS(user_id) ON DELETE CASCADE,
        job_id INTEGER REFERENCES JOBS(job_id) ON DELETE SET NULL,
        interview_type TEXT NOT NULL DEFAULT 'mixed' CHECK (interview_type IN ('technical', 'behavioral', 'mixed')),
        total_questions INTEGER NOT NULL DEFAULT 5,
        current_question_index INTEGER NOT NULL DEFAULT 0,
        overall_score REAL,
        overall_feedback TEXT,
        status TEXT NOT NULL DEFAULT 'in_progress' CHECK (status IN ('in_progress', 'completed', 'abandoned')),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS INTERVIEW_MESSAGES (
        message_id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER NOT NULL REFERENCES INTERVIEW_SESSIONS(session_id) ON DELETE CASCADE,
        question_number INTEGER NOT NULL,
        question_text TEXT NOT NULL,
        student_answer TEXT,
        score INTEGER,
        strengths TEXT,
        improvements TEXT,
        sample_answer TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS ROADMAPS (
        roadmap_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES USERS(user_id) ON DELETE CASCADE,
        job_id INTEGER REFERENCES JOBS(job_id) ON DELETE SET NULL,
        total_weeks INTEGER NOT NULL,
        hours_per_week INTEGER NOT NULL,
        roadmap_data TEXT,
        completed_topics TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS GENERATED_DOCUMENTS (
        doc_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES USERS(user_id) ON DELETE CASCADE,
        job_id INTEGER REFERENCES JOBS(job_id) ON DELETE SET NULL,
        doc_type TEXT NOT NULL CHECK (doc_type IN ('cover_letter', 'email', 'interview_prep')),
        tone TEXT NOT NULL DEFAULT 'formal' CHECK (tone IN ('formal', 'friendly', 'confident')),
        content TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS REPORTED_POSTINGS (
        report_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES USERS(user_id) ON DELETE CASCADE,
        job_id INTEGER REFERENCES JOBS(job_id) ON DELETE SET NULL,
        reason TEXT NOT NULL,
        details TEXT,
        status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'reviewed', 'dismissed')),
        reviewed_by INTEGER REFERENCES USERS(user_id) ON DELETE SET NULL,
        reviewed_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """
    with sqlite3.connect(_sqlite_path) as conn:
        conn.executescript(sqlite_schema)
        conn.commit()
