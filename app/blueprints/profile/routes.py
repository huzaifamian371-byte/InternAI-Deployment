from datetime import datetime
from flask import (
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    jsonify,
    abort,
)
from . import profile_bp
from app.blueprints.auth.routes import login_required, get_current_user
import db


def calculate_completeness(user, profile, skills_count, projects_count) -> int:
    """Calculate the profile completeness percentage (0 to 100)."""
    score = 0
    # Basic account info exists (20%)
    if user and user.get("full_name") and user.get("email"):
        score += 20
    # University and degree populated (25%)
    if profile:
        if profile.get("university") and profile.get("degree"):
            score += 25
        elif profile.get("university") or profile.get("degree"):
            score += 15
        # Bio populated (15%)
        if profile.get("bio") and len(profile.get("bio").strip()) >= 20:
            score += 15
        elif profile.get("bio"):
            score += 5
    # Skills added (up to 20%)
    if skills_count >= 5:
        score += 20
    elif skills_count > 0:
        score += int((skills_count / 5.0) * 20)
    # Projects added (up to 20%)
    if projects_count >= 2:
        score += 20
    elif projects_count == 1:
        score += 10

    return min(100, score)


@profile_bp.route("", methods=["GET"])
@profile_bp.route("/", methods=["GET"])
@login_required
def index():
    """Display student profile dashboard, skills catalog, and projects list."""
    user = get_current_user()
    user_id = user["user_id"]

    # 1. Fetch or create student profile record
    profile = db.query_one(
        "SELECT profile_id, university, degree, graduation_year, graduation_date, gpa, bio, cv_filename, cv_uploaded_at "
        "FROM STUDENT_PROFILES WHERE user_id = :uid",
        {"uid": user_id},
    )
    if not profile:
        db.execute(
            "INSERT INTO STUDENT_PROFILES (user_id) VALUES (:uid)",
            {"uid": user_id},
        )
        profile = db.query_one(
            "SELECT profile_id, university, degree, graduation_year, graduation_date, gpa, bio, cv_filename, cv_uploaded_at "
            "FROM STUDENT_PROFILES WHERE user_id = :uid",
            {"uid": user_id},
        )

    # 2. Fetch student's assigned skills
    student_skills = db.query_all(
        "SELECT ss.student_skill_id, ss.skill_id, ss.proficiency, ss.verified, "
        "       s.name AS skill_name, s.category AS skill_category, s.description AS skill_desc "
        "FROM STUDENT_SKILLS ss "
        "JOIN SKILLS s ON ss.skill_id = s.skill_id "
        "WHERE ss.user_id = :uid "
        "ORDER BY s.category ASC, s.name ASC",
        {"uid": user_id},
    )

    # 3. Fetch student's projects
    projects = db.query_all(
        "SELECT project_id, title, description, tech_used, project_url, created_at "
        "FROM PROJECTS "
        "WHERE user_id = :uid "
        "ORDER BY created_at DESC",
        {"uid": user_id},
    )

    # 4. Fetch all available skills from catalog for autocomplete dropdown
    all_catalog_skills = db.query_all(
        "SELECT skill_id, name, category FROM SKILLS ORDER BY name ASC"
    )

    completeness = calculate_completeness(user, profile, len(student_skills), len(projects))

    # Group student skills by category for nice pill UI display
    skills_by_category = {}
    for sk in student_skills:
        cat = sk.get("skill_category", "other")
        skills_by_category.setdefault(cat, []).append(sk)

    return render_template(
        "profile/index.html",
        user=user,
        profile=profile,
        student_skills=student_skills,
        skills_by_category=skills_by_category,
        projects=projects,
        catalog_skills=all_catalog_skills,
        completeness=completeness,
    )


@profile_bp.route("/update", methods=["POST"])
@login_required
def update_profile():
    """Update student academic details and bio."""
    user = get_current_user()
    user_id = user["user_id"]

    full_name = request.form.get("full_name", "").strip()
    university = request.form.get("university", "").strip()
    degree = request.form.get("degree", "").strip()
    graduation_year = request.form.get("graduation_year", "").strip()
    graduation_date = request.form.get("graduation_date", "").strip()
    gpa_raw = request.form.get("gpa", "").strip()
    bio = request.form.get("bio", "").strip()

    # Parse and validate optional numeric fields
    grad_year = None
    if graduation_year:
        try:
            grad_year = int(graduation_year)
            if grad_year < 1980 or grad_year > 2040:
                flash("Please enter a realistic graduation year (1980-2040).", "warning")
                return redirect(url_for("profile.index"))
        except ValueError:
            flash("Graduation year must be a valid number.", "warning")
            return redirect(url_for("profile.index"))

    gpa = None
    if gpa_raw:
        try:
            gpa = float(gpa_raw)
            if gpa < 0.0 or gpa > 4.0:
                flash("GPA must be between 0.00 and 4.00.", "warning")
                return redirect(url_for("profile.index"))
        except ValueError:
            flash("GPA must be a valid numeric value.", "warning")
            return redirect(url_for("profile.index"))

    grad_date = None
    if graduation_date:
        try:
            datetime.strptime(graduation_date, "%Y-%m-%d")
            grad_date = graduation_date
        except ValueError:
            pass

    # Update full_name in USERS table if changed
    if full_name:
        db.execute(
            "UPDATE USERS SET full_name = :fname, updated_at = CURRENT_TIMESTAMP WHERE user_id = :uid",
            {"fname": full_name, "uid": user_id},
        )
        session["user_name"] = full_name

    # Update STUDENT_PROFILES table
    db.execute(
        "UPDATE STUDENT_PROFILES "
        "SET university = :univ, degree = :deg, graduation_year = :gyear, "
        "    graduation_date = :gdate, gpa = :gpa, bio = :bio, updated_at = CURRENT_TIMESTAMP "
        "WHERE user_id = :uid",
        {
            "univ": university or None,
            "deg": degree or None,
            "gyear": grad_year,
            "gdate": grad_date,
            "gpa": gpa,
            "bio": bio or None,
            "uid": user_id,
        },
    )

    flash("Your profile details have been saved successfully!", "success")
    return redirect(url_for("profile.index"))


@profile_bp.route("/skills/add", methods=["POST"])
@login_required
def add_skill():
    """Add a skill with proficiency to the student profile."""
    user = get_current_user()
    user_id = user["user_id"]

    is_json = request.is_json
    data = request.get_json(silent=True) if is_json else request.form

    skill_name = (data.get("skill_name") or "").strip()
    proficiency = (data.get("proficiency") or "Intermediate").capitalize()
    category = (data.get("category") or "tools").lower()

    if proficiency not in ("Beginner", "Intermediate", "Advanced"):
        proficiency = "Intermediate"

    if not skill_name:
        if is_json:
            return jsonify({"success": False, "error": "Skill name is required."}), 400
        flash("Please provide a skill name.", "danger")
        return redirect(url_for("profile.index"))

    # Find skill in catalog or insert new skill
    skill_row = db.query_one(
        "SELECT skill_id, name FROM SKILLS WHERE LOWER(name) = LOWER(:name)",
        {"name": skill_name},
    )

    if not skill_row:
        valid_categories = ('programming', 'web', 'data', 'cloud', 'soft_skills', 'tools')
        if category not in valid_categories:
            category = "tools"
        skill_id = db.execute_returning_id(
            "INSERT INTO SKILLS (name, category, description) VALUES (:name, :category, :desc)",
            {
                "name": skill_name,
                "category": category,
                "desc": f"Custom skill added by user: {skill_name}",
            },
            id_column="skill_id",
        )
    else:
        skill_id = skill_row["skill_id"]

    # Check if student already has this skill
    existing = db.query_one(
        "SELECT student_skill_id FROM STUDENT_SKILLS WHERE user_id = :uid AND skill_id = :sid",
        {"uid": user_id, "sid": skill_id},
    )

    if existing:
        db.execute(
            "UPDATE STUDENT_SKILLS SET proficiency = :prof WHERE student_skill_id = :ssid",
            {"prof": proficiency, "ssid": existing["student_skill_id"]},
        )
        msg = f"Updated proficiency for '{skill_name}' to {proficiency}."
    else:
        db.execute(
            "INSERT INTO STUDENT_SKILLS (user_id, skill_id, proficiency) VALUES (:uid, :sid, :prof)",
            {"uid": user_id, "sid": skill_id, "prof": proficiency},
        )
        msg = f"Added '{skill_name}' ({proficiency}) to your profile skills."

    if is_json:
        return jsonify({"success": True, "message": msg})
    flash(msg, "success")
    return redirect(url_for("profile.index"))


@profile_bp.route("/skills/<int:student_skill_id>/delete", methods=["POST"])
@login_required
def delete_skill(student_skill_id: int):
    """Remove a skill from the student profile."""
    user = get_current_user()
    user_id = user["user_id"]

    deleted = db.execute(
        "DELETE FROM STUDENT_SKILLS WHERE student_skill_id = :ssid AND user_id = :uid",
        {"ssid": student_skill_id, "uid": user_id},
    )

    if request.is_json:
        if deleted:
            return jsonify({"success": True, "message": "Skill removed successfully."})
        return jsonify({"success": False, "error": "Skill not found or permission denied."}), 404

    if deleted:
        flash("Skill removed from your profile.", "info")
    else:
        flash("Could not delete skill: record not found.", "warning")
    return redirect(url_for("profile.index"))


@profile_bp.route("/skills/<int:student_skill_id>/update", methods=["POST"])
@login_required
def update_skill_proficiency(student_skill_id: int):
    """Update proficiency level for an existing student skill."""
    user = get_current_user()
    user_id = user["user_id"]

    proficiency = request.form.get("proficiency", "Intermediate").capitalize()
    if proficiency not in ("Beginner", "Intermediate", "Advanced"):
        proficiency = "Intermediate"

    db.execute(
        "UPDATE STUDENT_SKILLS SET proficiency = :prof WHERE student_skill_id = :ssid AND user_id = :uid",
        {"prof": proficiency, "ssid": student_skill_id, "uid": user_id},
    )
    flash(f"Proficiency updated to {proficiency}.", "success")
    return redirect(url_for("profile.index"))


@profile_bp.route("/projects/add", methods=["POST"])
@login_required
def add_project():
    """Add a new portfolio or university project."""
    user = get_current_user()
    user_id = user["user_id"]

    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    tech_used = request.form.get("tech_used", "").strip()
    project_url = request.form.get("project_url", "").strip()

    if not title:
        flash("Project title is required.", "danger")
        return redirect(url_for("profile.index"))

    db.execute(
        "INSERT INTO PROJECTS (user_id, title, description, tech_used, project_url) "
        "VALUES (:uid, :title, :desc, :tech, :url)",
        {
            "uid": user_id,
            "title": title,
            "desc": description or None,
            "tech": tech_used or None,
            "url": project_url or None,
        },
    )
    flash(f"Project '{title}' added to your portfolio.", "success")
    return redirect(url_for("profile.index") + "#projects")


@profile_bp.route("/projects/<int:project_id>/update", methods=["POST"])
@login_required
def update_project(project_id: int):
    """Update an existing portfolio project."""
    user = get_current_user()
    user_id = user["user_id"]

    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    tech_used = request.form.get("tech_used", "").strip()
    project_url = request.form.get("project_url", "").strip()

    if not title:
        flash("Project title is required.", "danger")
        return redirect(url_for("profile.index") + "#projects")

    updated = db.execute(
        "UPDATE PROJECTS "
        "SET title = :title, description = :desc, tech_used = :tech, "
        "    project_url = :url, updated_at = CURRENT_TIMESTAMP "
        "WHERE project_id = :pid AND user_id = :uid",
        {
            "title": title,
            "desc": description or None,
            "tech": tech_used or None,
            "url": project_url or None,
            "pid": project_id,
            "uid": user_id,
        },
    )

    if updated:
        flash(f"Project '{title}' updated successfully.", "success")
    else:
        flash("Project not found or permission denied.", "warning")
    return redirect(url_for("profile.index") + "#projects")


@profile_bp.route("/projects/<int:project_id>/delete", methods=["POST"])
@login_required
def delete_project(project_id: int):
    """Delete a portfolio project."""
    user = get_current_user()
    user_id = user["user_id"]

    deleted = db.execute(
        "DELETE FROM PROJECTS WHERE project_id = :pid AND user_id = :uid",
        {"pid": project_id, "uid": user_id},
    )

    if deleted:
        flash("Project removed from your portfolio.", "info")
    else:
        flash("Project not found or permission denied.", "warning")
    return redirect(url_for("profile.index") + "#projects")


@profile_bp.route("/api/search-skills", methods=["GET"])
@login_required
def search_skills():
    """API endpoint to search catalog skills for typeahead input."""
    q = request.args.get("q", "").strip()
    if not q:
        rows = db.query_all("SELECT skill_id, name, category FROM SKILLS ORDER BY name ASC")
    else:
        rows = db.query_all(
            "SELECT skill_id, name, category FROM SKILLS "
            "WHERE LOWER(name) LIKE :query ORDER BY name ASC",
            {"query": f"%{q.lower()}%"},
        )
    return jsonify(rows)
