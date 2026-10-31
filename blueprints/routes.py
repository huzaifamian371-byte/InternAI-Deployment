import datetime
import logging
from typing import Any, Dict, List, Optional
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
from . import tracker_bp
from app.blueprints.auth.routes import login_required, get_current_user
import db

logger = logging.getLogger("internai.tracker")

VALID_STATUSES = ["Saved", "Applied", "Interview", "Offer", "Closed"]


def _calculate_deadline_meta(deadline_str: Optional[str], status: str) -> Dict[str, Any]:
    """Calculate urgency status and relative days remaining for a deadline."""
    if not deadline_str:
        return {"badge": None, "days_left": None, "label": "No deadline"}

    try:
        # Format might be 'YYYY-MM-DD' or ISO timestamp
        deadline_date = datetime.datetime.strptime(str(deadline_str)[:10], "%Y-%m-%d").date()
    except Exception:
        return {"badge": None, "days_left": None, "label": deadline_str}

    today = datetime.date.today()
    delta = (deadline_date - today).days

    if status in ("Offer", "Closed"):
        return {"badge": "neutral", "days_left": delta, "label": f"{deadline_date.strftime('%b %d')}"}

    if delta < 0:
        return {"badge": "danger", "days_left": delta, "label": f"Overdue ({abs(delta)}d ago)"}
    elif delta == 0:
        return {"badge": "warning", "days_left": 0, "label": "Due Today!"}
    elif delta <= 3:
        return {"badge": "warning", "days_left": delta, "label": f"Due in {delta}d"}
    elif delta <= 7:
        return {"badge": "info", "days_left": delta, "label": f"Due in {delta}d"}
    else:
        return {"badge": "default", "days_left": delta, "label": f"{deadline_date.strftime('%b %d')}"}


@tracker_bp.route("/")
@login_required
def index():
    """Kanban board view: displays applications grouped across 5 stage columns."""
    user = get_current_user()
    user_id = user["user_id"]

    # Fetch all applications for the student
    apps = db.query_all(
        "SELECT a.application_id, a.user_id, a.job_id, a.company_name, a.role_title, "
        "       a.status, a.application_date, a.deadline, a.notes, a.created_at, a.updated_at, "
        "       j.title AS job_title, j.company AS job_company, j.location AS job_location "
        "FROM APPLICATIONS a "
        "LEFT JOIN JOBS j ON a.job_id = j.job_id "
        "WHERE a.user_id = :uid "
        "ORDER BY a.updated_at DESC",
        {"uid": user_id},
    )

    # Initialize columns
    columns = {
        "Saved": [],
        "Applied": [],
        "Interview": [],
        "Offer": [],
        "Closed": [],
    }

    upcoming_deadlines_count = 0

    for item in apps:
        status = item.get("status") or "Saved"
        if status not in columns:
            status = "Saved"

        deadline_meta = _calculate_deadline_meta(item.get("deadline"), status)
        item["deadline_meta"] = deadline_meta

        if deadline_meta.get("badge") in ("danger", "warning"):
            upcoming_deadlines_count += 1

        columns[status].append(item)

    # User's analyzed jobs for quick linking in "Add Application" modal
    jobs = db.query_all(
        "SELECT job_id, title, company FROM JOBS WHERE user_id = :uid ORDER BY created_at DESC",
        {"uid": user_id},
    )

    stats = {
        "total": len(apps),
        "saved": len(columns["Saved"]),
        "applied": len(columns["Applied"]),
        "interview": len(columns["Interview"]),
        "offer": len(columns["Offer"]),
        "closed": len(columns["Closed"]),
        "upcoming_deadlines": upcoming_deadlines_count,
    }

    return render_template(
        "tracker/index.html",
        columns=columns,
        stats=stats,
        jobs=jobs,
        valid_statuses=VALID_STATUSES,
    )


@tracker_bp.route("/add", methods=["POST"])
@login_required
def add_application():
    """Create a new internship application entry."""
    user = get_current_user()
    user_id = user["user_id"]

    company_name = request.form.get("company_name", "").strip()
    role_title = request.form.get("role_title", "").strip()
    status = request.form.get("status", "Saved").strip()
    if status not in VALID_STATUSES:
        status = "Saved"

    app_date = request.form.get("application_date", "").strip() or None
    deadline = request.form.get("deadline", "").strip() or None
    notes = request.form.get("notes", "").strip() or None
    job_id_raw = request.form.get("job_id", "").strip()
    job_id = int(job_id_raw) if job_id_raw and job_id_raw.isdigit() else None

    if not company_name or not role_title:
        flash("Company name and role title are required.", "danger")
        return redirect(url_for("tracker.index"))

    app_id = db.execute_returning_id(
        "INSERT INTO APPLICATIONS ("
        "  user_id, job_id, company_name, role_title, status, application_date, deadline, notes"
        ") VALUES ("
        "  :uid, :jid, :comp, :role, :status, :app_date, :deadline, :notes"
        ")",
        {
            "uid": user_id,
            "jid": job_id,
            "comp": company_name,
            "role": role_title,
            "status": status,
            "app_date": app_date,
            "deadline": deadline,
            "notes": notes,
        },
        id_column="application_id",
    )

    flash(f"Application for '{role_title}' at {company_name} saved to pipeline!", "success")
    return redirect(url_for("tracker.index"))


@tracker_bp.route("/update-status/<int:app_id>", methods=["POST"])
@login_required
def update_status(app_id: int):
    """AJAX endpoint for drag-and-drop Kanban status transitions."""
    user = get_current_user()
    user_id = user["user_id"]

    # Support JSON or form payload
    if request.is_json:
        data = request.get_json() or {}
        new_status = data.get("status", "").strip()
    else:
        new_status = request.form.get("status", "").strip()

    if new_status not in VALID_STATUSES:
        return jsonify({"error": f"Invalid status: {new_status}"}), 400

    row = db.query_one(
        "SELECT application_id FROM APPLICATIONS WHERE application_id = :aid AND user_id = :uid",
        {"aid": app_id, "uid": user_id},
    )
    if not row:
        return jsonify({"error": "Application not found."}), 404

    db.execute(
        "UPDATE APPLICATIONS SET status = :status, updated_at = CURRENT_TIMESTAMP "
        "WHERE application_id = :aid",
        {"status": new_status, "aid": app_id},
    )

    return jsonify({"success": True, "application_id": app_id, "new_status": new_status})


@tracker_bp.route("/application/<int:app_id>")
@login_required
def get_application(app_id: int):
    """Return application details as JSON for editing modal."""
    user = get_current_user()
    user_id = user["user_id"]

    app_row = db.query_one(
        "SELECT * FROM APPLICATIONS WHERE application_id = :aid AND user_id = :uid",
        {"aid": app_id, "uid": user_id},
    )
    if not app_row:
        return jsonify({"error": "Application not found"}), 404

    # Format dates to YYYY-MM-DD
    if app_row.get("application_date"):
        app_row["application_date"] = str(app_row["application_date"])[:10]
    if app_row.get("deadline"):
        app_row["deadline"] = str(app_row["deadline"])[:10]

    return jsonify(app_row)


@tracker_bp.route("/update/<int:app_id>", methods=["POST"])
@login_required
def update_application(app_id: int):
    """Update details of an existing application."""
    user = get_current_user()
    user_id = user["user_id"]

    row = db.query_one(
        "SELECT application_id FROM APPLICATIONS WHERE application_id = :aid AND user_id = :uid",
        {"aid": app_id, "uid": user_id},
    )
    if not row:
        flash("Application not found.", "warning")
        return redirect(url_for("tracker.index"))

    company_name = request.form.get("company_name", "").strip()
    role_title = request.form.get("role_title", "").strip()
    status = request.form.get("status", "Saved").strip()
    if status not in VALID_STATUSES:
        status = "Saved"

    app_date = request.form.get("application_date", "").strip() or None
    deadline = request.form.get("deadline", "").strip() or None
    notes = request.form.get("notes", "").strip() or None
    job_id_raw = request.form.get("job_id", "").strip()
    job_id = int(job_id_raw) if job_id_raw and job_id_raw.isdigit() else None

    if not company_name or not role_title:
        flash("Company and role title are required.", "danger")
        return redirect(url_for("tracker.index"))

    db.execute(
        "UPDATE APPLICATIONS SET "
        "  company_name = :comp, role_title = :role, status = :status, "
        "  application_date = :adate, deadline = :dline, notes = :notes, "
        "  job_id = :jid, updated_at = CURRENT_TIMESTAMP "
        "WHERE application_id = :aid AND user_id = :uid",
        {
            "comp": company_name,
            "role": role_title,
            "status": status,
            "adate": app_date,
            "dline": deadline,
            "notes": notes,
            "jid": job_id,
            "aid": app_id,
            "uid": user_id,
        },
    )

    flash("Application updated successfully.", "success")
    return redirect(url_for("tracker.index"))


@tracker_bp.route("/delete/<int:app_id>", methods=["POST"])
@login_required
def delete_application(app_id: int):
    """Remove an application from the tracker."""
    user = get_current_user()
    user_id = user["user_id"]

    existing = db.query_one(
        "SELECT application_id FROM APPLICATIONS WHERE application_id = :aid AND user_id = :uid",
        {"aid": app_id, "uid": user_id},
    )
    if not existing:
        flash("Application not found or unauthorized.", "warning")
        return redirect(url_for("tracker.index"))

    db.execute(
        "DELETE FROM APPLICATIONS WHERE application_id = :aid AND user_id = :uid",
        {"aid": app_id, "uid": user_id},
    )

    flash("Application removed from tracker.", "info")
    return redirect(url_for("tracker.index"))


@tracker_bp.route("/quick-add", methods=["POST"])
@login_required
def quick_add():
    """One-click add an analyzed job into the tracker (defaults to 'Saved' column)."""
    user = get_current_user()
    user_id = user["user_id"]

    job_id_raw = request.form.get("job_id", "").strip()
    if not job_id_raw or not job_id_raw.isdigit():
        flash("Invalid job selected.", "danger")
        return redirect(url_for("tracker.index"))

    job_id = int(job_id_raw)
    job = db.query_one(
        "SELECT title, company FROM JOBS WHERE job_id = :jid AND user_id = :uid",
        {"jid": job_id, "uid": user_id},
    )
    if not job:
        flash("Job not found.", "warning")
        return redirect(url_for("tracker.index"))

    # Check if already added
    existing = db.query_one(
        "SELECT application_id FROM APPLICATIONS WHERE user_id = :uid AND job_id = :jid",
        {"uid": user_id, "jid": job_id},
    )
    if existing:
        flash("This job is already in your application pipeline.", "info")
        return redirect(url_for("tracker.index"))

    db.execute_returning_id(
        "INSERT INTO APPLICATIONS ("
        "  user_id, job_id, company_name, role_title, status"
        ") VALUES ("
        "  :uid, :jid, :comp, :role, 'Saved'"
        ")",
        {
            "uid": user_id,
            "jid": job_id,
            "comp": job.get("company", "Company"),
            "role": job.get("title", "Role"),
        },
        id_column="application_id",
    )

    flash(f"'{job.get('title')}' added to Saved applications!", "success")
    return redirect(url_for("tracker.index"))
