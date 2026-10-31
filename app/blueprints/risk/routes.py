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
from . import risk_bp
from app.blueprints.auth.routes import login_required, get_current_user
from app.services import ai_service
import db


@risk_bp.route("", methods=["GET"])
@risk_bp.route("/", methods=["GET"])
@login_required
def index():
    """Display the Scam & Job Risk Analyzer input portal and history."""
    user = get_current_user()
    user_id = user["user_id"]

    jobs = db.query_all(
        "SELECT j.job_id, j.title, j.company, j.location, j.work_type, j.created_at, "
        "       ja.risk_level, ja.risk_score, ja.risk_summary "
        "FROM JOBS j "
        "LEFT JOIN JOB_ANALYSIS ja ON j.job_id = ja.job_id "
        "WHERE j.user_id = :uid "
        "ORDER BY j.created_at DESC",
        {"uid": user_id},
    )

    return render_template("risk/index.html", jobs=jobs)


@risk_bp.route("/check", methods=["POST"])
@login_required
def check_standalone():
    """Run risk analysis on un-saved pasted text."""
    job_text = request.form.get("job_text", "").strip()

    if not job_text or len(job_text) < 30:
        flash("Please provide a job posting text with at least 30 characters for analysis.", "danger")
        return redirect(url_for("risk.index"))

    risk_result = ai_service.analyze_job_risk(job_text)

    return render_template(
        "risk/view.html",
        risk_result=risk_result,
        job=None,
        job_text=job_text,
    )


@risk_bp.route("/<int:job_id>", methods=["GET"])
@login_required
def view_job_risk(job_id: int):
    """Run or view fraud & scam risk evaluation for an analyzed job."""
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

    # Perform risk analysis on job raw text
    risk_result = ai_service.analyze_job_risk(
        job_text=job["raw_text"],
        company_name=job.get("company"),
    )

    # Persist risk level and score in database
    db.execute(
        "UPDATE JOB_ANALYSIS "
        "SET risk_score = :rscore, risk_level = :rlvl, risk_summary = :rsum "
        "WHERE job_id = :jid",
        {
            "rscore": risk_result["risk_score"],
            "rlvl": risk_result["risk_level"],
            "rsum": risk_result["summary"],
            "jid": job_id,
        },
    )

    # Check if user already reported this posting
    existing_report = db.query_one(
        "SELECT report_id, status FROM REPORTED_POSTINGS WHERE user_id = :uid AND job_id = :jid",
        {"uid": user_id, "jid": job_id},
    )

    return render_template(
        "risk/view.html",
        job=job,
        analysis=analysis,
        risk_result=risk_result,
        job_text=job["raw_text"],
        existing_report=existing_report,
    )


@risk_bp.route("/report/<int:job_id>", methods=["POST"])
@login_required
def report_posting(job_id: int):
    """Flag a suspicious job posting for administrator safety review."""
    user = get_current_user()
    user_id = user["user_id"]

    reason = request.form.get("reason", "").strip()
    details = request.form.get("details", "").strip()

    if not reason:
        flash("Please specify a reason for reporting this posting.", "danger")
        return redirect(url_for("risk.view_job_risk", job_id=job_id))

    # Check if already reported
    existing = db.query_one(
        "SELECT report_id FROM REPORTED_POSTINGS WHERE user_id = :uid AND job_id = :jid",
        {"uid": user_id, "jid": job_id},
    )

    if existing:
        flash("You have already reported this job posting. Our admin team has queued it for review.", "info")
    else:
        db.execute(
            "INSERT INTO REPORTED_POSTINGS (user_id, job_id, reason, details) "
            "VALUES (:uid, :jid, :reason, :details)",
            {
                "uid": user_id,
                "jid": job_id,
                "reason": reason,
                "details": details or None,
            },
        )
        flash("Report submitted successfully. Thank you for protecting the university student community.", "success")

    return redirect(url_for("risk.view_job_risk", job_id=job_id))
