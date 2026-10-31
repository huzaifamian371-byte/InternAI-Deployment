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
from . import analyzer_bp
from app.blueprints.auth.routes import login_required, get_current_user
from app.services import ai_service
import db


@analyzer_bp.route("/", methods=["GET"])
@login_required
def index():
    """Display the Job Analyzer submission form and student's recent analyses."""
    user = get_current_user()
    user_id = user["user_id"]

    # Fetch existing analyzed jobs for this student
    jobs = db.query_all(
        "SELECT j.job_id, j.title, j.company, j.location, j.work_type, j.stipend, j.created_at, "
        "       ja.match_percentage, ja.risk_level "
        "FROM JOBS j "
        "LEFT JOIN JOB_ANALYSIS ja ON j.job_id = ja.job_id "
        "WHERE j.user_id = :uid "
        "ORDER BY j.created_at DESC",
        {"uid": user_id},
    )

    return render_template("analyzer/index.html", jobs=jobs)


@analyzer_bp.route("/parse", methods=["POST"])
@login_required
def parse():
    """Submit a job posting for AI structured extraction and analysis."""
    user = get_current_user()
    user_id = user["user_id"]

    raw_text = request.form.get("raw_text", "").strip()

    if not raw_text or len(raw_text) < 30:
        flash("Please paste a complete job description (minimum 30 characters).", "danger")
        return redirect(url_for("analyzer.index"))

    try:
        # 1. Run AI analysis
        analysis_data = ai_service.analyze_job_description(raw_text)

        title = analysis_data.get("job_title", "Internship Opportunity")
        company = analysis_data.get("company", "Company")
        location = analysis_data.get("location", "Not specified")
        work_type = analysis_data.get("work_type", "unspecified")
        duration = analysis_data.get("duration", "Not specified")
        stipend = analysis_data.get("stipend", "Not specified")
        role_summary = analysis_data.get("role_summary", "")
        exp_req = analysis_data.get("experience_required", "")
        edu_req = analysis_data.get("education_required", "")

        # 2. Insert into JOBS table
        job_id = db.execute_returning_id(
            "INSERT INTO JOBS ("
            "  user_id, title, company, location, work_type, duration, stipend, "
            "  raw_text, role_summary, experience_required, education_required"
            ") VALUES ("
            "  :uid, :title, :company, :location, :wtype, :dur, :stipend, "
            "  :raw_text, :summary, :exp, :edu"
            ")",
            {
                "uid": user_id,
                "title": title,
                "company": company,
                "location": location,
                "wtype": work_type,
                "dur": duration,
                "stipend": stipend,
                "raw_text": raw_text,
                "summary": role_summary,
                "exp": exp_req,
                "edu": edu_req,
            },
            id_column="job_id",
        )

        # 3. Insert normalized skills into JOB_REQUIREMENTS
        req_skills = analysis_data.get("required_skills", [])
        for skill in req_skills:
            if skill.strip():
                db.execute(
                    "INSERT INTO JOB_REQUIREMENTS (job_id, skill_name, req_type, importance) "
                    "VALUES (:jid, :skill, 'required', 'critical')",
                    {"jid": job_id, "skill": skill.strip()},
                )

        pref_skills = analysis_data.get("preferred_skills", [])
        for skill in pref_skills:
            if skill.strip():
                db.execute(
                    "INSERT INTO JOB_REQUIREMENTS (job_id, skill_name, req_type, importance) "
                    "VALUES (:jid, :skill, 'preferred', 'nice_to_have')",
                    {"jid": job_id, "skill": skill.strip()},
                )

        # 4. Save analysis to JOB_ANALYSIS table
        structured_json_str = json.dumps(analysis_data)
        db.execute(
            "INSERT INTO JOB_ANALYSIS (job_id, user_id, structured_json) "
            "VALUES (:jid, :uid, :json_data)",
            {
                "jid": job_id,
                "uid": user_id,
                "json_data": structured_json_str,
            },
        )

        flash(f"Successfully analyzed '{title}' at {company}!", "success")
        return redirect(url_for("analyzer.view_job", job_id=job_id))

    except Exception as exc:
        current_app.logger.error("Error analyzing job posting: %s", exc, exc_info=True)
        flash(f"An error occurred while analyzing the job posting: {str(exc)}", "danger")
        return redirect(url_for("analyzer.index"))


@analyzer_bp.route("/<int:job_id>", methods=["GET"])
@login_required
def view_job(job_id: int):
    """Display comprehensive structured analysis for a specific job."""
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

    requirements = db.query_all(
        "SELECT * FROM JOB_REQUIREMENTS WHERE job_id = :jid ORDER BY req_type DESC, skill_name ASC",
        {"jid": job_id},
    )

    structured_data = {}
    if analysis and analysis.get("structured_json"):
        try:
            raw_json = analysis["structured_json"]
            structured_data = json.loads(raw_json) if isinstance(raw_json, str) else raw_json
        except Exception:
            structured_data = {}

    return render_template(
        "analyzer/view.html",
        job=job,
        analysis=analysis,
        requirements=requirements,
        structured_data=structured_data,
    )


@analyzer_bp.route("/<int:job_id>/delete", methods=["POST"])
@login_required
def delete_job(job_id: int):
    """Delete an analyzed job posting and its linked records."""
    user = get_current_user()
    user_id = user["user_id"]

    deleted = db.execute(
        "DELETE FROM JOBS WHERE job_id = :jid AND user_id = :uid",
        {"jid": job_id, "uid": user_id},
    )

    if deleted:
        flash("Job analysis deleted.", "info")
    else:
        flash("Could not delete job analysis.", "warning")

    return redirect(url_for("analyzer.index"))
