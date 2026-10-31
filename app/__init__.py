import logging
import os
from flask import Flask, render_template, session, redirect, url_for
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from config import config_by_name, Config
import db

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("internai")

csrf = CSRFProtect()
limiter = Limiter(
    key_func=get_remote_address,
    storage_uri="memory://"
)


def create_app(config_name: str = None) -> Flask:
    """Application factory for InternAI."""
    app = Flask(__name__)
    
    # Determine configuration
    if config_name is None:
        config_name = os.getenv("FLASK_ENV", "development")
    cfg = config_by_name.get(config_name, Config)
    app.config.from_object(cfg)

    # Initialize extensions
    csrf.init_app(app)
    limiter.init_app(app)

    # Ensure uploads directory exists
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    # Initialize database pool
    db.init_pool(app.config)

    # Register blueprints
    from app.blueprints.auth import auth_bp
    from app.blueprints.profile import profile_bp
    from app.blueprints.analyzer import analyzer_bp
    from app.blueprints.matcher import matcher_bp
    from app.blueprints.roadmap import roadmap_bp
    from app.blueprints.risk import risk_bp
    from app.blueprints.cv import cv_bp
    from app.blueprints.assistant import assistant_bp
    from app.blueprints.interview import interview_bp
    from app.blueprints.tracker import tracker_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(profile_bp)
    app.register_blueprint(analyzer_bp)
    app.register_blueprint(matcher_bp)
    app.register_blueprint(roadmap_bp)
    app.register_blueprint(risk_bp)
    app.register_blueprint(cv_bp)
    app.register_blueprint(assistant_bp)
    app.register_blueprint(interview_bp)
    app.register_blueprint(tracker_bp)

    # Context processors
    from app.blueprints.auth.routes import get_current_user

    @app.context_processor
    def inject_global_vars():
        user = get_current_user()
        return {
            "current_user": user,
            "db_backend": db.get_backend(),
            "app_name": "InternAI",
        }

    # Root route: landing page or dashboard
    @app.route("/")
    def index():
        user = get_current_user()
        if user:
            # Gather quick metrics for student dashboard
            user_id = user["user_id"]
            skills_count = db.query_one(
                "SELECT COUNT(*) as cnt FROM STUDENT_SKILLS WHERE user_id = :uid",
                {"uid": user_id}
            )
            projects_count = db.query_one(
                "SELECT COUNT(*) as cnt FROM PROJECTS WHERE user_id = :uid",
                {"uid": user_id}
            )
            apps_count = db.query_one(
                "SELECT COUNT(*) as cnt FROM APPLICATIONS WHERE user_id = :uid",
                {"uid": user_id}
            )
            recent_jobs = db.query_all(
                "SELECT j.job_id, j.title, j.company, j.location, j.work_type, j.created_at, "
                "       ja.match_percentage, ja.risk_level "
                "FROM JOBS j "
                "LEFT JOIN JOB_ANALYSIS ja ON j.job_id = ja.job_id "
                "WHERE j.user_id = :uid "
                "ORDER BY j.created_at DESC FETCH FIRST 5 ROWS ONLY",
                {"uid": user_id}
            ) if db.get_backend() == "oracle" else db.query_all(
                "SELECT j.job_id, j.title, j.company, j.location, j.work_type, j.created_at, "
                "       ja.match_percentage, ja.risk_level "
                "FROM JOBS j "
                "LEFT JOIN JOB_ANALYSIS ja ON j.job_id = ja.job_id "
                "WHERE j.user_id = :uid "
                "ORDER BY j.created_at DESC LIMIT 5",
                {"uid": user_id}
            )

            profile = db.query_one(
                "SELECT * FROM STUDENT_PROFILES WHERE user_id = :uid",
                {"uid": user_id}
            )

            from app.blueprints.profile.routes import calculate_completeness
            completeness = calculate_completeness(
                user,
                profile,
                skills_count["cnt"] if skills_count else 0,
                projects_count["cnt"] if projects_count else 0
            )

            return render_template(
                "index.html",
                is_dashboard=True,
                user=user,
                profile=profile,
                skills_count=skills_count["cnt"] if skills_count else 0,
                projects_count=projects_count["cnt"] if projects_count else 0,
                apps_count=apps_count["cnt"] if apps_count else 0,
                recent_jobs=recent_jobs,
                completeness=completeness
            )
        return render_template("index.html", is_dashboard=False)

    # Centralized error handlers
    @app.errorhandler(400)
    def bad_request_error(error):
        return render_template("errors/400.html", error=error), 400

    @app.errorhandler(403)
    def forbidden_error(error):
        return render_template("errors/403.html", error=error), 403

    @app.errorhandler(404)
    def not_found_error(error):
        return render_template("errors/404.html", error=error), 404

    @app.errorhandler(429)
    def ratelimit_error(error):
        return render_template("errors/429.html", error=error), 429

    @app.errorhandler(500)
    def internal_server_error(error):
        logger.error("Internal Server Error: %s", error, exc_info=True)
        return render_template("errors/500.html", error=error), 500

    return app
