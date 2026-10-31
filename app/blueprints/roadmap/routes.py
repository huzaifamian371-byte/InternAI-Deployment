import json
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
from . import roadmap_bp
from app.blueprints.auth.routes import login_required, get_current_user
from app.services import ai_service
import db


@roadmap_bp.route("", methods=["GET"])
@roadmap_bp.route("/", methods=["GET"])
@login_required
def index():
    """List student's generated roadmaps and available target jobs."""
    user = get_current_user()
    user_id = user["user_id"]

    # 1. Fetch existing roadmaps
    roadmaps_raw = db.query_all(
        "SELECT r.roadmap_id, r.total_weeks, r.hours_per_week, r.roadmap_data, r.completed_topics, "
        "       r.created_at, j.job_id, j.title AS job_title, j.company "
        "FROM ROADMAPS r "
        "LEFT JOIN JOBS j ON r.job_id = j.job_id "
        "WHERE r.user_id = :uid "
        "ORDER BY r.created_at DESC",
        {"uid": user_id},
    )

    roadmaps = []
    for r in roadmaps_raw:
        # Calculate completion %
        total_topics = 0
        try:
            r_data = json.loads(r["roadmap_data"]) if isinstance(r["roadmap_data"], str) else (r["roadmap_data"] or {})
            for w in r_data.get("weeks", []):
                total_topics += len(w.get("topics", []))
        except Exception:
            pass

        completed_list = []
        try:
            completed_list = json.loads(r["completed_topics"]) if isinstance(r["completed_topics"], str) else (r["completed_topics"] or [])
        except Exception:
            pass

        pct = round((len(completed_list) / max(total_topics, 1)) * 100.0, 1) if total_topics > 0 else 0.0

        roadmaps.append({
            **r,
            "total_topics": total_topics,
            "completed_count": len(completed_list),
            "progress_percent": pct,
        })

    # 2. Fetch analyzed jobs that don't have a roadmap yet
    available_jobs = db.query_all(
        "SELECT j.job_id, j.title, j.company, j.location, ja.match_percentage "
        "FROM JOBS j "
        "LEFT JOIN JOB_ANALYSIS ja ON j.job_id = ja.job_id "
        "WHERE j.user_id = :uid "
        "ORDER BY j.created_at DESC",
        {"uid": user_id},
    )

    return render_template(
        "roadmap/index.html",
        roadmaps=roadmaps,
        available_jobs=available_jobs,
    )


@roadmap_bp.route("/generate/<int:job_id>", methods=["GET", "POST"])
@login_required
def generate(job_id: int):
    """Customize weeks and hours commitment, then generate the AI roadmap."""
    user = get_current_user()
    user_id = user["user_id"]

    job = db.query_one(
        "SELECT * FROM JOBS WHERE job_id = :jid AND user_id = :uid",
        {"jid": job_id, "uid": user_id},
    )
    if not job:
        abort(404)

    # Fetch missing skills for this job
    req_rows = db.query_all(
        "SELECT skill_name, req_type, importance FROM JOB_REQUIREMENTS WHERE job_id = :jid",
        {"jid": job_id},
    )
    required_skills = [r["skill_name"] for r in req_rows if r["req_type"] == "required"]
    preferred_skills = [r["skill_name"] for r in req_rows if r["req_type"] == "preferred"]

    student_skills = db.query_all(
        "SELECT ss.student_skill_id, s.name AS skill_name, ss.proficiency "
        "FROM STUDENT_SKILLS ss JOIN SKILLS s ON ss.skill_id = s.skill_id "
        "WHERE ss.user_id = :uid",
        {"uid": user_id},
    )

    match_res = ai_service.match_skills(student_skills, required_skills, preferred_skills)
    missing_skills = match_res.get("missing_skills", [])

    if request.method == "POST":
        try:
            total_weeks = int(request.form.get("total_weeks", 4))
            hours_per_week = int(request.form.get("hours_per_week", 10))
        except ValueError:
            total_weeks = 4
            hours_per_week = 10

        total_weeks = max(1, min(16, total_weeks))
        hours_per_week = max(2, min(40, hours_per_week))

        # Generate roadmap via AI
        roadmap_data = ai_service.generate_learning_roadmap(
            job_title=job["title"],
            missing_skills=missing_skills,
            total_weeks=total_weeks,
            hours_per_week=hours_per_week,
        )

        roadmap_json_str = json.dumps(roadmap_data)
        empty_completed = json.dumps([])

        # Save to ROADMAPS table
        roadmap_id = db.execute_returning_id(
            "INSERT INTO ROADMAPS (user_id, job_id, total_weeks, hours_per_week, roadmap_data, completed_topics) "
            "VALUES (:uid, :jid, :weeks, :hours, :data, :completed)",
            {
                "uid": user_id,
                "jid": job_id,
                "weeks": total_weeks,
                "hours": hours_per_week,
                "data": roadmap_json_str,
                "completed": empty_completed,
            },
            id_column="roadmap_id",
        )

        flash(f"Successfully generated your {total_weeks}-week study roadmap for {job['title']}!", "success")
        return redirect(url_for("roadmap.view_roadmap", roadmap_id=roadmap_id))

    return render_template(
        "roadmap/generate.html",
        job=job,
        missing_skills=missing_skills,
        student_skills=student_skills,
    )


@roadmap_bp.route("/<int:roadmap_id>", methods=["GET"])
@login_required
def view_roadmap(roadmap_id: int):
    """View interactive timeline for a learning roadmap with topic completion toggles."""
    user = get_current_user()
    user_id = user["user_id"]

    roadmap = db.query_one(
        "SELECT r.*, j.title AS job_title, j.company, j.location "
        "FROM ROADMAPS r "
        "LEFT JOIN JOBS j ON r.job_id = j.job_id "
        "WHERE r.roadmap_id = :rid AND r.user_id = :uid",
        {"rid": roadmap_id, "uid": user_id},
    )
    if not roadmap:
        abort(404)

    roadmap_data = {}
    try:
        raw_data = roadmap["roadmap_data"]
        roadmap_data = json.loads(raw_data) if isinstance(raw_data, str) else (raw_data or {})
    except Exception:
        roadmap_data = {}

    completed_topics = []
    try:
        raw_comp = roadmap["completed_topics"]
        completed_topics = json.loads(raw_comp) if isinstance(raw_comp, str) else (raw_comp or [])
    except Exception:
        completed_topics = []

    # Calculate completion metrics
    total_topics = 0
    for w in roadmap_data.get("weeks", []):
        total_topics += len(w.get("topics", []))

    percent_done = round((len(completed_topics) / max(total_topics, 1)) * 100.0, 1) if total_topics > 0 else 0.0

    return render_template(
        "roadmap/view.html",
        roadmap=roadmap,
        roadmap_data=roadmap_data,
        completed_topics=completed_topics,
        total_topics=total_topics,
        percent_done=percent_done,
    )


@roadmap_bp.route("/<int:roadmap_id>/toggle-topic", methods=["POST"])
@login_required
def toggle_topic(roadmap_id: int):
    """Toggle completion status of a topic milestone (AJAX endpoint)."""
    user = get_current_user()
    user_id = user["user_id"]

    roadmap = db.query_one(
        "SELECT roadmap_id, roadmap_data, completed_topics FROM ROADMAPS WHERE roadmap_id = :rid AND user_id = :uid",
        {"rid": roadmap_id, "uid": user_id},
    )
    if not roadmap:
        return jsonify({"success": False, "error": "Roadmap not found"}), 404

    is_json = request.is_json
    data = request.get_json(silent=True) if is_json else request.form
    topic_id = (data.get("topic_id") or "").strip()

    if not topic_id:
        return jsonify({"success": False, "error": "topic_id is required"}), 400

    completed_list = []
    try:
        raw_comp = roadmap["completed_topics"]
        completed_list = json.loads(raw_comp) if isinstance(raw_comp, str) else (raw_comp or [])
    except Exception:
        completed_list = []

    # Toggle state
    if topic_id in completed_list:
        completed_list.remove(topic_id)
        is_completed = False
    else:
        completed_list.append(topic_id)
        is_completed = True

    # Persist in DB
    db.execute(
        "UPDATE ROADMAPS SET completed_topics = :comp, updated_at = CURRENT_TIMESTAMP WHERE roadmap_id = :rid",
        {"comp": json.dumps(completed_list), "rid": roadmap_id},
    )

    # Compute updated progress
    total_topics = 0
    try:
        r_data = json.loads(roadmap["roadmap_data"]) if isinstance(roadmap["roadmap_data"], str) else (roadmap["roadmap_data"] or {})
        for w in r_data.get("weeks", []):
            total_topics += len(w.get("topics", []))
    except Exception:
        pass

    percent_done = round((len(completed_list) / max(total_topics, 1)) * 100.0, 1) if total_topics > 0 else 0.0

    return jsonify({
        "success": True,
        "topic_id": topic_id,
        "is_completed": is_completed,
        "completed_count": len(completed_list),
        "total_topics": total_topics,
        "percent_done": percent_done,
    })


@roadmap_bp.route("/<int:roadmap_id>/delete", methods=["POST"])
@login_required
def delete_roadmap(roadmap_id: int):
    """Delete a saved learning roadmap."""
    user = get_current_user()
    user_id = user["user_id"]

    deleted = db.execute(
        "DELETE FROM ROADMAPS WHERE roadmap_id = :rid AND user_id = :uid",
        {"rid": roadmap_id, "uid": user_id},
    )

    if deleted:
        flash("Learning roadmap deleted.", "info")
    else:
        flash("Could not delete roadmap.", "warning")

    return redirect(url_for("roadmap.index"))
