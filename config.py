import os
from datetime import timedelta
from pathlib import Path
from dotenv import load_dotenv

# Base directory of the project
BASE_DIR = Path(__file__).resolve().parent

# Load .env file
load_dotenv(BASE_DIR / ".env")


class Config:
    """Base application configuration."""
    SECRET_KEY = os.getenv("SECRET_KEY", "internai-super-secret-key-change-in-production")
    
    # Session Security
    PERMANENT_SESSION_LIFETIME = timedelta(
        hours=int(os.getenv("SESSION_TIMEOUT_HOURS", 2))
    )
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SECURE = False  # Set to True in production with HTTPS
    SESSION_COOKIE_SAMESITE = "Lax"
    
    # CSRF Protection
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = 7200  # 2 hours
    
    # Database Settings
    DB_BACKEND = os.getenv("DB_BACKEND", "auto").lower()  # 'oracle', 'sqlite', or 'auto'
    SQLITE_DB_PATH = str(BASE_DIR / os.getenv("SQLITE_DB_PATH", "internai.db"))
    
    # Oracle Database Settings (Thin mode)
    ORACLE_USER = os.getenv("ORACLE_USER", "internai_admin")
    ORACLE_PASSWORD = os.getenv("ORACLE_PASSWORD", "InternAIPassword2026#")
    ORACLE_DSN = os.getenv("ORACLE_DSN", "localhost:1521/FREEPDB1")
    ORACLE_POOL_MIN = int(os.getenv("ORACLE_POOL_MIN", 2))
    ORACLE_POOL_MAX = int(os.getenv("ORACLE_POOL_MAX", 10))
    ORACLE_POOL_INC = int(os.getenv("ORACLE_POOL_INC", 1))
    
    # Gemini AI Settings
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    
    # Upload Settings
    UPLOAD_FOLDER = str(BASE_DIR / os.getenv("UPLOAD_FOLDER", "uploads"))
    MAX_CONTENT_LENGTH = int(os.getenv("MAX_CONTENT_LENGTH", 5 * 1024 * 1024))  # 5 MB
    ALLOWED_EXTENSIONS = {"pdf", "docx"}
    
    # Rate Limiting
    RATELIMIT_DEFAULT = os.getenv("RATELIMIT_DEFAULT", "200/day;50/hour")
    RATELIMIT_AI = os.getenv("RATELIMIT_AI", "15/minute")


class DevelopmentConfig(Config):
    """Development configuration."""
    DEBUG = True
    TESTING = False


class TestingConfig(Config):
    """Testing configuration."""
    DEBUG = False
    TESTING = True
    WTF_CSRF_ENABLED = False
    DB_BACKEND = "sqlite"
    SQLITE_DB_PATH = str(BASE_DIR / "test_internai.db")


class ProductionConfig(Config):
    """Production configuration."""
    DEBUG = False
    TESTING = False
    SESSION_COOKIE_SECURE = True


config_by_name = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}
