import json
from flask import (
    render_template,
    request,
    redirect,
    url_for,
    flash,
    abort,
    current_app,
)
from . import matcher_bp
from app.blueprints.auth.routes import login_required, get_current_user
from app.services import ai_service
import db


@matcher_bp.route("/", methods=["GET"])
@login_required
def index():
    """Display list of analyzed jobs with their skill match status."""
    user = get_current_user()
    user_id = user["user_id"]

    jobs = db.query_all(
        "SELECT j.job_id, j.title, j.company, j.location, j.work_type, j.created_at, "
        "       ja.match_percentage, ja.risk_level, "
        "       (SELECT COUNT(*) FROM JOB_REQUIREMENTS jr WHERE jr.job_id = j.job_id) AS req_count "
        "FROM JOBS j "
        "LEFT JOIN JOB_ANALYSIS ja ON j.job_id = ja.job_id "
        "WHERE j.user_id = :uid "
        "ORDER BY j.created_at DESC",
        {"uid": user_id},
    )

    # Check student skills count
    skills_count = db.query_one(
        "SELECT COUNT(*) AS cnt FROM STUDENT_SKILLS WHERE user_id = :uid",
        {"uid": user_id},
    )

    return render_template(
        "matcher/index.html",
        jobs=jobs,
        student_skills_count=skills_count["cnt"] if skills_count else 0,
    )


@matcher_bp.route("/<int:job_id>", methods=["GET"])
@login_required
def view_match(job_id: int):
    """Run or display the semantic skill match for a specific job posting."""
    user = get_current_user()
    user_id = user["user_id"]

    job = db.query_one(
        "SELECT * FROM JOBS WHERE job_id = :jid AND user_id = :uid",
        {"jid": job_id, "uid": user_id},
    )
    if not job:
        abort(404)

    analysis = db.query_one(
        "SELECT * FROM JOB_ANALYSIS WHERE job_id = :jid",
        {"jid": job_id},
    )

    # 1. Fetch requirements for this job
    req_rows = db.query_all(
        "SELECT skill_name, req_type, importance FROM JOB_REQUIREMENTS WHERE job_id = :jid",
        {"jid": job_id},
    )

    required_skills = [r["skill_name"] for r in req_rows if r["req_type"] == "required"]
    preferred_skills = [r["skill_name"] for r in req_rows if r["req_type"] == "preferred"]

    # 2. Fetch student's profile skills
    student_skills = db.query_all(
        "SELECT ss.student_skill_id, ss.skill_id, ss.proficiency, s.name AS skill_name, s.category "
        "FROM STUDENT_SKILLS ss "
        "JOIN SKILLS s ON ss.skill_id = s.skill_id "
        "WHERE ss.user_id = :uid "
        "ORDER BY s.name ASC",
        {"uid": user_id},
    )

    # 3. Perform semantic skill matching
    match_result = ai_service.match_skills(
        student_skills=student_skills,
        required_skills=required_skills,
        preferred_skills=preferred_skills,
    )

    match_percentage = match_result["match_percentage"]

    # 4. Persist calculated match percentage in JOB_ANALYSIS table
    db.execute(
        "UPDATE JOB_ANALYSIS SET match_percentage = :pct WHERE job_id = :jid",
        {"pct": match_percentage, "jid": job_id},
    )

    return render_template(
        "matcher/view.html",
        job=job,
        analysis=analysis,
        match_result=match_result,
        student_skills=student_skills,
        required_skills=required_skills,
        preferred_skills=preferred_skills,
    )


@matcher_bp.route("/<int:job_id>/recalculate", methods=["POST"])
@login_required
def recalculate(job_id: int):
    """Force re-evaluation of skills match (e.g. after student adds new skills to profile)."""
    flash("Skill match recalculated against your updated profile skills.", "info")
    return redirect(url_for("matcher.view_match", job_id=job_id))
