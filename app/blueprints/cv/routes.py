import json
import logging
from flask import (
    render_template,
    request,
    redirect,
    url_for,
    flash,
    abort,
    jsonify,
    current_app,
)
from . import cv_bp
from app.blueprints.auth.routes import login_required, get_current_user
from app.services import cv_service, ai_service
import db

logger = logging.getLogger("internai.cv")


@cv_bp.route("", methods=["GET"])
@cv_bp.route("/", methods=["GET"])
@login_required
def index():
    """CV Analyzer dashboard: shows uploaded CV status, past analyses, and analysis launchpad."""
    user = get_current_user()
    user_id = user["user_id"]

    # 1. Fetch profile CV info
    profile = db.query_one(
        "SELECT cv_filename, cv_uploaded_at FROM STUDENT_PROFILES WHERE user_id = :uid",
        {"uid": user_id},
    )

    # 2. Fetch past CV evaluations
    analyses_raw = db.query_all(
        "SELECT c.cv_analysis_id, c.cv_score, c.score_breakdown, c.created_at, "
        "       j.job_id, j.title AS job_title, j.company "
        "FROM CV_ANALYSIS c "
        "LEFT JOIN JOBS j ON c.job_id = j.job_id "
        "WHERE c.user_id = :uid "
        "ORDER BY c.created_at DESC",
        {"uid": user_id},
    )

    analyses = []
    for a in analyses_raw:
        breakdown = {}
        try:
            b_raw = a["score_breakdown"]
            breakdown = json.loads(b_raw) if isinstance(b_raw, str) else (b_raw or {})
        except Exception:
            pass
        analyses.append({
            **a,
            "breakdown": breakdown,
        })

    # 3. Fetch analyzed jobs for targeting
    available_jobs = db.query_all(
        "SELECT j.job_id, j.title, j.company, j.location "
        "FROM JOBS j "
        "WHERE j.user_id = :uid "
        "ORDER BY j.created_at DESC",
        {"uid": user_id},
    )

    return render_template(
        "cv/index.html",
        profile=profile,
        analyses=analyses,
        available_jobs=available_jobs,
    )


@cv_bp.route("/upload", methods=["POST"])
@login_required
def upload_cv():
    """Upload and parse a PDF or DOCX resume document."""
    user = get_current_user()
    user_id = user["user_id"]

    if "cv_file" not in request.files:
        flash("No file part provided in upload.", "danger")
        return redirect(url_for("cv.index"))

    file = request.files["cv_file"]
    if not file or file.filename == "":
        flash("Please select a valid PDF or DOCX file to upload.", "danger")
        return redirect(url_for("cv.index"))

    target_job_id = request.form.get("job_id")
    job_id = int(target_job_id) if target_job_id and target_job_id.isdigit() else None

    # Save and extract document text
    try:
        saved_filename, saved_path, extracted_text = cv_service.save_and_extract_cv(
            file, current_app.config["UPLOAD_FOLDER"]
        )
    except ValueError as e:
        flash(f"Upload error: {str(e)}", "danger")
        return redirect(url_for("cv.index"))
    except Exception as e:
        logger.error(f"Unexpected error uploading CV: {e}", exc_info=True)
        flash("An unexpected error occurred while extracting text from your resume.", "danger")
        return redirect(url_for("cv.index"))

    # Update STUDENT_PROFILES with active CV filename
    db.execute(
        "UPDATE STUDENT_PROFILES "
        "SET cv_filename = :fname, cv_uploaded_at = CURRENT_TIMESTAMP "
        "WHERE user_id = :uid",
        {"fname": saved_filename, "uid": user_id},
    )

    # Gather job context if target job was selected
    job_title = None
    job_requirements = []
    if job_id:
        job = db.query_one(
            "SELECT title, company FROM JOBS WHERE job_id = :jid AND user_id = :uid",
            {"jid": job_id, "uid": user_id},
        )
        if job:
            job_title = f"{job['title']} at {job['company']}"
            req_rows = db.query_all(
                "SELECT skill_name FROM JOB_REQUIREMENTS WHERE job_id = :jid",
                {"jid": job_id},
            )
            job_requirements = [r["skill_name"] for r in req_rows]

    # Run AI CV analysis
    analysis_data = ai_service.analyze_cv_text(
        cv_text=extracted_text,
        job_title=job_title,
        job_requirements=job_requirements,
    )

    # Persist in CV_ANALYSIS
    score_obj = analysis_data.get("score", {})
    total_score = score_obj.get("total_score", 70)

    extracted_entities = {
        "candidate_name": analysis_data.get("candidate_name"),
        "contact_email": analysis_data.get("contact_email"),
        "contact_phone": analysis_data.get("contact_phone"),
        "summary_verdict": analysis_data.get("summary_verdict"),
        "extracted_skills": analysis_data.get("extracted_skills", {}),
        "education": analysis_data.get("education", []),
        "experience": analysis_data.get("experience", []),
        "projects": analysis_data.get("projects", []),
    }

    suggestions_obj = {
        "weak_or_missing_sections": analysis_data.get("weak_or_missing_sections", []),
        "highlighted_projects": analysis_data.get("highlighted_projects", []),
        "bullet_rewrites": analysis_data.get("bullet_rewrites", []),
    }

    analysis_id = db.execute_returning_id(
        "INSERT INTO CV_ANALYSIS (user_id, job_id, raw_cv_text, extracted_data, cv_score, score_breakdown, suggestions) "
        "VALUES (:uid, :jid, :raw, :data, :score, :breakdown, :sugg)",
        {
            "uid": user_id,
            "jid": job_id,
            "raw": extracted_text,
            "data": json.dumps(extracted_entities),
            "score": total_score,
            "breakdown": json.dumps(score_obj),
            "sugg": json.dumps(suggestions_obj),
        },
        id_column="cv_analysis_id",
    )

    flash("Resume successfully uploaded and evaluated with AI!", "success")
    return redirect(url_for("cv.view_analysis", analysis_id=analysis_id))


@cv_bp.route("/paste", methods=["POST"])
@login_required
def paste_cv():
    """Analyze CV from raw text pasted into form."""
    user = get_current_user()
    user_id = user["user_id"]

    cv_text = request.form.get("cv_text", "").strip()
    if not cv_text or len(cv_text) < 40:
        flash("Please paste your resume content with at least 40 characters.", "danger")
        return redirect(url_for("cv.index"))

    target_job_id = request.form.get("job_id")
    job_id = int(target_job_id) if target_job_id and target_job_id.isdigit() else None

    job_title = None
    job_requirements = []
    if job_id:
        job = db.query_one(
            "SELECT title, company FROM JOBS WHERE job_id = :jid AND user_id = :uid",
            {"jid": job_id, "uid": user_id},
        )
        if job:
            job_title = f"{job['title']} at {job['company']}"
            req_rows = db.query_all(
                "SELECT skill_name FROM JOB_REQUIREMENTS WHERE job_id = :jid",
                {"jid": job_id},
            )
            job_requirements = [r["skill_name"] for r in req_rows]

    analysis_data = ai_service.analyze_cv_text(
        cv_text=cv_text,
        job_title=job_title,
        job_requirements=job_requirements,
    )

    score_obj = analysis_data.get("score", {})
    total_score = score_obj.get("total_score", 70)

    extracted_entities = {
        "candidate_name": analysis_data.get("candidate_name"),
        "contact_email": analysis_data.get("contact_email"),
        "contact_phone": analysis_data.get("contact_phone"),
        "summary_verdict": analysis_data.get("summary_verdict"),
        "extracted_skills": analysis_data.get("extracted_skills", {}),
        "education": analysis_data.get("education", []),
        "experience": analysis_data.get("experience", []),
        "projects": analysis_data.get("projects", []),
    }

    suggestions_obj = {
        "weak_or_missing_sections": analysis_data.get("weak_or_missing_sections", []),
        "highlighted_projects": analysis_data.get("highlighted_projects", []),
        "bullet_rewrites": analysis_data.get("bullet_rewrites", []),
    }

    analysis_id = db.execute_returning_id(
        "INSERT INTO CV_ANALYSIS (user_id, job_id, raw_cv_text, extracted_data, cv_score, score_breakdown, suggestions) "
        "VALUES (:uid, :jid, :raw, :data, :score, :breakdown, :sugg)",
        {
            "uid": user_id,
            "jid": job_id,
            "raw": cv_text,
            "data": json.dumps(extracted_entities),
            "score": total_score,
            "breakdown": json.dumps(score_obj),
            "sugg": json.dumps(suggestions_obj),
        },
        id_column="cv_analysis_id",
    )

    flash("Resume text successfully evaluated with AI!", "success")
    return redirect(url_for("cv.view_analysis", analysis_id=analysis_id))


@cv_bp.route("/<int:analysis_id>", methods=["GET"])
@login_required
def view_analysis(analysis_id: int):
    """View detailed score breakdown, bullet rewrites, and skill import portal."""
    user = get_current_user()
    user_id = user["user_id"]

    analysis = db.query_one(
        "SELECT c.*, j.title AS job_title, j.company, j.location "
        "FROM CV_ANALYSIS c "
        "LEFT JOIN JOBS j ON c.job_id = j.job_id "
        "WHERE c.cv_analysis_id = :aid AND c.user_id = :uid",
        {"aid": analysis_id, "uid": user_id},
    )
    if not analysis:
        abort(404)

    # Parse JSON fields safely
    def _parse(val):
        if isinstance(val, str):
            try:
                return json.loads(val)
            except Exception:
                return {}
        return val or {}

    extracted_data = _parse(analysis["extracted_data"])
    score_breakdown = _parse(analysis["score_breakdown"])
    suggestions = _parse(analysis["suggestions"])

    # Fetch student's existing skills to mark which skills are already in profile
    existing_skills_rows = db.query_all(
        "SELECT s.name FROM STUDENT_SKILLS ss JOIN SKILLS s ON ss.skill_id = s.skill_id WHERE ss.user_id = :uid",
        {"uid": user_id},
    )
    existing_skill_names = {r["name"].lower() for r in existing_skills_rows}

    # Group extracted skills
    extracted_skills_dict = extracted_data.get("extracted_skills", {})
    all_extracted_skills = []
    for cat, slist in extracted_skills_dict.items():
        for s in slist:
            all_extracted_skills.append({
                "name": s,
                "category": cat,
                "already_in_profile": s.lower() in existing_skill_names,
            })

    return render_template(
        "cv/view.html",
        analysis=analysis,
        extracted_data=extracted_data,
        score_breakdown=score_breakdown,
        suggestions=suggestions,
        all_extracted_skills=all_extracted_skills,
    )


@cv_bp.route("/import-skills/<int:analysis_id>", methods=["POST"])
@login_required
def import_skills(analysis_id: int):
    """Import chosen skills from CV analysis directly into student profile."""
    user = get_current_user()
    user_id = user["user_id"]

    analysis = db.query_one(
        "SELECT cv_analysis_id, extracted_data FROM CV_ANALYSIS WHERE cv_analysis_id = :aid AND user_id = :uid",
        {"aid": analysis_id, "uid": user_id},
    )
    if not analysis:
        abort(404)

    selected_skills = request.form.getlist("skills")
    default_proficiency = request.form.get("proficiency", "Intermediate")
    if default_proficiency not in ("Beginner", "Intermediate", "Advanced"):
        default_proficiency = "Intermediate"

    if not selected_skills:
        flash("No skills selected for import.", "warning")
        return redirect(url_for("cv.view_analysis", analysis_id=analysis_id))

    imported_count = 0
    for skill_name in selected_skills:
        skill_name = skill_name.strip()
        if not skill_name:
            continue

        # 1. Find or create in master SKILLS table
        skill_record = db.query_one(
            "SELECT skill_id FROM SKILLS WHERE LOWER(name) = LOWER(:name)",
            {"name": skill_name},
        )
        if not skill_record:
            skill_id = db.execute_returning_id(
                "INSERT INTO SKILLS (name, category, description) "
                "VALUES (:name, 'tools', 'Extracted from student CV')",
                {"name": skill_name},
                id_column="skill_id",
            )
        else:
            skill_id = skill_record["skill_id"]

        # 2. Check if already attached to student
        existing = db.query_one(
            "SELECT student_skill_id FROM STUDENT_SKILLS WHERE user_id = :uid AND skill_id = :sid",
            {"uid": user_id, "sid": skill_id},
        )
        if not existing:
            db.execute(
                "INSERT INTO STUDENT_SKILLS (user_id, skill_id, proficiency, verified) "
                "VALUES (:uid, :sid, :prof, 0)",
                {
                    "uid": user_id,
                    "sid": skill_id,
                    "prof": default_proficiency,
                },
            )
            imported_count += 1

    flash(f"Successfully imported {imported_count} new skill(s) to your profile!", "success")
    return redirect(url_for("cv.view_analysis", analysis_id=analysis_id))


@cv_bp.route("/<int:analysis_id>/delete", methods=["POST"])
@login_required
def delete_analysis(analysis_id: int):
    """Delete a past CV evaluation record."""
    user = get_current_user()
    user_id = user["user_id"]

    deleted = db.execute(
        "DELETE FROM CV_ANALYSIS WHERE cv_analysis_id = :aid AND user_id = :uid",
        {"aid": analysis_id, "uid": user_id},
    )

    if deleted:
        flash("CV analysis deleted.", "info")
    else:
        flash("Could not delete CV analysis.", "warning")

    return redirect(url_for("cv.index"))
