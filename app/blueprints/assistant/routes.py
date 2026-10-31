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
from . import assistant_bp
from app.blueprints.auth.routes import login_required, get_current_user
from app.services import ai_service
import db

logger = logging.getLogger("internai.assistant")


def _get_student_full_profile(user_id: int) -> dict:
    """Retrieve full student background (academics, skills, portfolio projects) for grounding AI."""
    user = db.query_one("SELECT full_name, email FROM USERS WHERE user_id = :uid", {"uid": user_id})
    profile = db.query_one("SELECT * FROM STUDENT_PROFILES WHERE user_id = :uid", {"uid": user_id}) or {}
    
    skills = db.query_all(
        "SELECT s.name AS skill_name, s.category, ss.proficiency "
        "FROM STUDENT_SKILLS ss JOIN SKILLS s ON ss.skill_id = s.skill_id "
        "WHERE ss.user_id = :uid",
        {"uid": user_id},
    )
    
    projects = db.query_all(
        "SELECT title, description, tech_used, project_url "
        "FROM PROJECTS WHERE user_id = :uid",
        {"uid": user_id},
    )

    return {
        "full_name": user.get("full_name") if user else "Student",
        "email": user.get("email") if user else "",
        "university": profile.get("university") or "University",
        "degree": profile.get("degree") or "Computer Science",
        "graduation_year": profile.get("graduation_year") or "2026",
        "gpa": str(profile.get("gpa")) if profile.get("gpa") else None,
        "bio": profile.get("bio") or "",
        "skills": skills,
        "projects": projects,
    }


def _get_latest_cv_text(user_id: int) -> str | None:
    """Retrieve raw text from the student's most recent CV evaluation if available."""
    recent_cv = db.query_one(
        "SELECT raw_cv_text FROM CV_ANALYSIS WHERE user_id = :uid ORDER BY created_at DESC",
        {"uid": user_id},
    )
    return recent_cv["raw_cv_text"] if recent_cv else None


@assistant_bp.route("", methods=["GET"])
@assistant_bp.route("/", methods=["GET"])
@login_required
def index():
    """List student's generated cover letters, outreach emails, and interview guides."""
    user = get_current_user()
    user_id = user["user_id"]
    filter_type = request.args.get("type", "all")

    sql = (
        "SELECT d.doc_id, d.doc_type, d.tone, d.content, d.created_at, d.updated_at, "
        "       j.job_id, j.title AS job_title, j.company "
        "FROM GENERATED_DOCUMENTS d "
        "LEFT JOIN JOBS j ON d.job_id = j.job_id "
        "WHERE d.user_id = :uid "
    )
    params = {"uid": user_id}

    if filter_type in ("cover_letter", "email", "interview_prep"):
        sql += " AND d.doc_type = :dtype"
        params["dtype"] = filter_type

    sql += " ORDER BY d.updated_at DESC"
    docs = db.query_all(sql, params)

    # Fetch stats
    total_docs = len(docs) if filter_type == "all" else db.query_one(
        "SELECT COUNT(*) as cnt FROM GENERATED_DOCUMENTS WHERE user_id = :uid",
        {"uid": user_id},
    )["cnt"]

    return render_template(
        "assistant/index.html",
        documents=docs,
        filter_type=filter_type,
        total_docs=total_docs,
    )


@assistant_bp.route("/generate", methods=["GET", "POST"])
@login_required
def generate_view():
    """Select job, tone, and generate tailored application materials."""
    user = get_current_user()
    user_id = user["user_id"]

    # Preselected job from query parameter
    preselect_job_id = request.args.get("job_id")
    target_job_id = int(preselect_job_id) if preselect_job_id and preselect_job_id.isdigit() else None

    # Fetch student's analyzed jobs
    available_jobs = db.query_all(
        "SELECT j.job_id, j.title, j.company, j.location "
        "FROM JOBS j WHERE j.user_id = :uid ORDER BY j.created_at DESC",
        {"uid": user_id},
    )

    if request.method == "POST":
        sel_job_id = request.form.get("job_id")
        job_id = int(sel_job_id) if sel_job_id and sel_job_id.isdigit() else None
        doc_type = request.form.get("doc_type", "cover_letter")
        tone = request.form.get("tone", "formal")

        if doc_type not in ("cover_letter", "email", "interview_prep"):
            doc_type = "cover_letter"
        if tone not in ("formal", "friendly", "confident"):
            tone = "formal"

        # Load job info
        job_info = {}
        if job_id:
            job = db.query_one(
                "SELECT title, company, location, raw_text FROM JOBS WHERE job_id = :jid AND user_id = :uid",
                {"jid": job_id, "uid": user_id},
            )
            if job:
                job_info = {
                    "title": job["title"],
                    "company": job["company"],
                    "location": job["location"],
                    "raw_text": job["raw_text"][:2000],
                }
        
        # If no saved job, check custom manual inputs
        if not job_info:
            custom_title = request.form.get("custom_title", "").strip() or "Software Engineering Intern"
            custom_company = request.form.get("custom_company", "").strip() or "Technology Company"
            custom_notes = request.form.get("custom_notes", "").strip()
            job_info = {
                "title": custom_title,
                "company": custom_company,
                "location": "Remote / Onsite",
                "raw_text": custom_notes,
            }

        # Gather student profile & CV text for strict grounding
        student_profile = _get_student_full_profile(user_id)
        cv_text = _get_latest_cv_text(user_id)

        formatted_content = ""

        if doc_type == "cover_letter":
            res = ai_service.generate_cover_letter(
                student_profile=student_profile,
                job_info=job_info,
                cv_text=cv_text,
                tone=tone,
            )
            # Format cover letter text
            salutation = res.get("recipient_salutation", "Dear Hiring Team,")
            subject = res.get("subject_line", f"Application for {job_info.get('title')}")
            body = "\n\n".join(res.get("body_paragraphs", []))
            closing = res.get("closing_salutation", "Sincerely,")
            name = res.get("candidate_name", student_profile["full_name"])
            formatted_content = f"{subject}\n\n{salutation}\n\n{body}\n\n{closing}\n{name}"

        elif doc_type == "email":
            res = ai_service.generate_application_email(
                student_profile=student_profile,
                job_info=job_info,
                tone=tone,
            )
            subject = res.get("subject_line", f"Internship Inquiry: {job_info.get('title')}")
            salutation = res.get("salutation", "Dear Hiring Team,")
            body = res.get("email_body", "")
            cta = res.get("call_to_action", "")
            signoff = res.get("signoff", "Best regards,")
            name = res.get("candidate_name", student_profile["full_name"])
            formatted_content = f"Subject: {subject}\n\n{salutation}\n\n{body}\n\n{cta}\n\n{signoff}\n{name}"

        elif doc_type == "interview_prep":
            res = ai_service.generate_interview_prep(
                student_profile=student_profile,
                job_info=job_info,
                cv_text=cv_text,
            )
            # Format interview prep markdown
            lines = [
                f"# Interview Preparation: {job_info.get('title')} at {job_info.get('company')}",
                f"\n**Role Overview:** {res.get('role_overview', '')}\n",
                "## 💡 Top Strategic Preparation Tips",
            ]
            for tip in res.get("top_preparation_tips", []):
                lines.append(f"- {tip}")

            lines.append("\n## 🎯 Tailored Practice Questions & Talking Points\n")
            for q in res.get("questions", []):
                lines.append(f"### Q{q.get('question_number')}: [{q.get('category')}] {q.get('question')}")
                lines.append(f"**Why this matters to the interviewer:** {q.get('why_it_matters')}\n")
                lines.append("**Key Talking Points from Your Real Background:**")
                for tp in q.get("talking_points", []):
                    lines.append(f"  • {tp}")
                lines.append(f"\n**Recommended Answering Framework:** {q.get('sample_framework')}\n")

            formatted_content = "\n".join(lines)

        # Save to GENERATED_DOCUMENTS
        doc_id = db.execute_returning_id(
            "INSERT INTO GENERATED_DOCUMENTS (user_id, job_id, doc_type, tone, content) "
            "VALUES (:uid, :jid, :dtype, :tone, :content)",
            {
                "uid": user_id,
                "jid": job_id,
                "dtype": doc_type,
                "tone": tone,
                "content": formatted_content,
            },
            id_column="doc_id",
        )

        type_label = doc_type.replace("_", " ").title()
        flash(f"{type_label} successfully generated with {tone.title()} tone!", "success")
        return redirect(url_for("assistant.view_document", doc_id=doc_id))

    return render_template(
        "assistant/generate.html",
        available_jobs=available_jobs,
        target_job_id=target_job_id,
    )


@assistant_bp.route("/<int:doc_id>", methods=["GET"])
@login_required
def view_document(doc_id: int):
    """View and edit a generated application document."""
    user = get_current_user()
    user_id = user["user_id"]

    doc = db.query_one(
        "SELECT d.*, j.title AS job_title, j.company, j.location "
        "FROM GENERATED_DOCUMENTS d "
        "LEFT JOIN JOBS j ON d.job_id = j.job_id "
        "WHERE d.doc_id = :did AND d.user_id = :uid",
        {"did": doc_id, "uid": user_id},
    )
    if not doc:
        abort(404)

    return render_template("assistant/view.html", doc=doc)


@assistant_bp.route("/<int:doc_id>/edit", methods=["POST"])
@login_required
def edit_document(doc_id: int):
    """Save user modifications to a generated document."""
    user = get_current_user()
    user_id = user["user_id"]

    doc = db.query_one(
        "SELECT doc_id FROM GENERATED_DOCUMENTS WHERE doc_id = :did AND user_id = :uid",
        {"did": doc_id, "uid": user_id},
    )
    if not doc:
        abort(404)

    content = request.form.get("content", "").strip()
    if not content:
        flash("Document content cannot be empty.", "danger")
        return redirect(url_for("assistant.view_document", doc_id=doc_id))

    db.execute(
        "UPDATE GENERATED_DOCUMENTS "
        "SET content = :content, updated_at = CURRENT_TIMESTAMP "
        "WHERE doc_id = :did",
        {"content": content, "did": doc_id},
    )

    flash("Document changes successfully saved!", "success")
    return redirect(url_for("assistant.view_document", doc_id=doc_id))


@assistant_bp.route("/<int:doc_id>/delete", methods=["POST"])
@login_required
def delete_document(doc_id: int):
    """Delete a saved application document."""
    user = get_current_user()
    user_id = user["user_id"]

    deleted = db.execute(
        "DELETE FROM GENERATED_DOCUMENTS WHERE doc_id = :did AND user_id = :uid",
        {"did": doc_id, "uid": user_id},
    )

    if deleted:
        flash("Document deleted.", "info")
    else:
        flash("Could not delete document.", "warning")

    return redirect(url_for("assistant.index"))
