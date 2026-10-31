import json
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
from . import interview_bp
from app.blueprints.auth.routes import login_required, get_current_user
from app.services import ai_service
import db

logger = logging.getLogger("internai.interview")


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


@interview_bp.route("/")
@login_required
def index():
    """Mock interview lobby: select job, start new session, or resume active session."""
    user = get_current_user()
    user_id = user["user_id"]

    # Retrieve analyzed jobs for quick selection
    jobs = db.query_all(
        "SELECT job_id, title, company, location, work_type, created_at "
        "FROM JOBS WHERE user_id = :uid ORDER BY created_at DESC",
        {"uid": user_id},
    )

    # In-progress sessions that can be resumed
    active_sessions = db.query_all(
        "SELECT s.session_id, s.interview_type, s.total_questions, s.current_question_index, "
        "       s.status, s.created_at, s.updated_at, "
        "       j.title AS job_title, j.company AS job_company "
        "FROM INTERVIEW_SESSIONS s "
        "LEFT JOIN JOBS j ON s.job_id = j.job_id "
        "WHERE s.user_id = :uid AND s.status = 'in_progress' "
        "ORDER BY s.updated_at DESC",
        {"uid": user_id},
    )

    # Completed sessions for historical review
    completed_sessions = db.query_all(
        "SELECT s.session_id, s.interview_type, s.total_questions, s.overall_score, "
        "       s.status, s.created_at, s.updated_at, "
        "       j.title AS job_title, j.company AS job_company "
        "FROM INTERVIEW_SESSIONS s "
        "LEFT JOIN JOBS j ON s.job_id = j.job_id "
        "WHERE s.user_id = :uid AND s.status = 'completed' "
        "ORDER BY s.updated_at DESC",
        {"uid": user_id},
    )

    return render_template(
        "interview/index.html",
        jobs=jobs,
        active_sessions=active_sessions,
        completed_sessions=completed_sessions,
    )


@interview_bp.route("/start", methods=["POST"])
@login_required
def start():
    """Initialize a new mock interview session and generate the first question."""
    user = get_current_user()
    user_id = user["user_id"]

    job_id_raw = request.form.get("job_id", "").strip()
    interview_type = request.form.get("interview_type", "mixed").lower()
    if interview_type not in ("technical", "behavioral", "mixed"):
        interview_type = "mixed"

    try:
        total_questions = int(request.form.get("total_questions", 5))
        total_questions = max(3, min(10, total_questions))
    except (ValueError, TypeError):
        total_questions = 5

    # Determine job info
    job_info: Dict[str, Any] = {}
    job_id: Optional[int] = None

    if job_id_raw and job_id_raw.isdigit():
        job_id = int(job_id_raw)
        job_row = db.query_one(
            "SELECT * FROM JOBS WHERE job_id = :jid AND user_id = :uid",
            {"jid": job_id, "uid": user_id},
        )
        if job_row:
            job_info = {
                "title": job_row.get("title", "Software Engineering Intern"),
                "company": job_row.get("company", "Tech Company"),
                "location": job_row.get("location", "Remote"),
                "raw_text": job_row.get("raw_text", ""),
                "role_summary": job_row.get("role_summary", ""),
            }
        else:
            job_id = None

    if not job_info:
        # Custom role setup
        custom_role = request.form.get("custom_role", "").strip() or "Software Engineering Intern"
        custom_company = request.form.get("custom_company", "").strip() or "Technology Company"
        custom_desc = request.form.get("custom_desc", "").strip() or (
            f"Internship position for {custom_role} at {custom_company}."
        )
        job_info = {
            "title": custom_role,
            "company": custom_company,
            "raw_text": custom_desc,
            "role_summary": custom_desc,
        }
        # Save as a lightweight job record to maintain foreign key integrity
        job_id = db.execute_returning_id(
            "INSERT INTO JOBS (user_id, title, company, location, work_type, raw_text, role_summary) "
            "VALUES (:uid, :title, :company, 'Unspecified', 'unspecified', :raw, :summary)",
            {
                "uid": user_id,
                "title": custom_role,
                "company": custom_company,
                "raw": custom_desc,
                "summary": custom_desc[:500],
            },
            id_column="job_id",
        )

    student_profile = _get_student_full_profile(user_id)

    # 1. Create session record
    session_id = db.execute_returning_id(
        "INSERT INTO INTERVIEW_SESSIONS ("
        "  user_id, job_id, interview_type, total_questions, current_question_index, status"
        ") VALUES ("
        "  :uid, :jid, :itype, :tq, 1, 'in_progress'"
        ")",
        {
            "uid": user_id,
            "jid": job_id,
            "itype": interview_type,
            "tq": total_questions,
        },
        id_column="session_id",
    )

    # 2. Generate Question 1 via AI Service
    first_q_data = ai_service.generate_mock_interview_question(
        job_info=job_info,
        student_profile=student_profile,
        question_number=1,
        total_questions=total_questions,
        interview_type=interview_type,
        previous_questions=[],
    )

    q_text = first_q_data.get("question_text", "Tell me about yourself and why you are interested in this position.")

    # 3. Insert first question into INTERVIEW_MESSAGES
    db.execute(
        "INSERT INTO INTERVIEW_MESSAGES ("
        "  session_id, question_number, question_text"
        ") VALUES ("
        "  :sid, 1, :qtext"
        ")",
        {"sid": session_id, "qtext": q_text},
    )

    flash("Mock interview started! Question 1 is ready.", "success")
    return redirect(url_for("interview.session_view", session_id=session_id))


@interview_bp.route("/session/<int:session_id>")
@login_required
def session_view(session_id: int):
    """Interactive interview room: displays chat history and current question."""
    user = get_current_user()
    user_id = user["user_id"]

    session_row = db.query_one(
        "SELECT * FROM INTERVIEW_SESSIONS WHERE session_id = :sid AND user_id = :uid",
        {"sid": session_id, "uid": user_id},
    )
    if not session_row:
        flash("Interview session not found.", "warning")
        return redirect(url_for("interview.index"))

    # If already completed, redirect to final summary report
    if session_row["status"] == "completed":
        return redirect(url_for("interview.session_summary", session_id=session_id))

    # Fetch associated job details
    job_row = None
    if session_row.get("job_id"):
        job_row = db.query_one(
            "SELECT * FROM JOBS WHERE job_id = :jid",
            {"jid": session_row["job_id"]},
        )

    # Fetch all messages/turns for this session
    messages = db.query_all(
        "SELECT * FROM INTERVIEW_MESSAGES WHERE session_id = :sid ORDER BY question_number ASC",
        {"sid": session_id},
    )

    # Find the active unanswered question
    current_message = None
    for msg in messages:
        if not msg.get("student_answer"):
            current_message = msg
            break

    # If somehow all questions are answered but status isn't completed, finalize it
    if not current_message and messages:
        if len(messages) >= session_row["total_questions"]:
            return redirect(url_for("interview.session_summary", session_id=session_id))

    return render_template(
        "interview/session.html",
        interview_session=session_row,
        job=job_row,
        messages=messages,
        current_message=current_message,
    )


@interview_bp.route("/session/<int:session_id>/answer", methods=["POST"])
@login_required
def submit_answer(session_id: int):
    """Receive student answer, evaluate coaching feedback, and load or generate next question."""
    user = get_current_user()
    user_id = user["user_id"]

    session_row = db.query_one(
        "SELECT * FROM INTERVIEW_SESSIONS WHERE session_id = :sid AND user_id = :uid",
        {"sid": session_id, "uid": user_id},
    )
    if not session_row:
        return jsonify({"error": "Session not found."}), 404

    if session_row["status"] == "completed":
        return jsonify({"redirect_url": url_for("interview.session_summary", session_id=session_id)})

    student_answer = request.form.get("student_answer", "").strip()
    if not student_answer:
        # Check JSON payload if sent via AJAX fetch
        if request.is_json:
            data = request.get_json() or {}
            student_answer = data.get("student_answer", "").strip()

    if not student_answer:
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"error": "Please provide an answer before submitting."}), 400
        flash("Please provide an answer before submitting.", "warning")
        return redirect(url_for("interview.session_view", session_id=session_id))

    # Identify the current unanswered question
    active_message = db.query_one(
        "SELECT * FROM INTERVIEW_MESSAGES WHERE session_id = :sid AND student_answer IS NULL ORDER BY question_number ASC",
        {"sid": session_id},
    )
    if not active_message:
        return jsonify({"redirect_url": url_for("interview.session_summary", session_id=session_id)})

    # Fetch job context
    role_title = "Software Engineering Intern"
    company = "Technology Company"
    if session_row.get("job_id"):
        job = db.query_one("SELECT title, company FROM JOBS WHERE job_id = :jid", {"jid": session_row["job_id"]})
        if job:
            role_title = job.get("title") or role_title
            company = job.get("company") or company

    # 1. AI Coaching Evaluation of the answer
    feedback = ai_service.evaluate_mock_interview_answer(
        question_text=active_message["question_text"],
        student_answer=student_answer,
        role_title=role_title,
        company=company,
        interview_type=session_row["interview_type"],
        question_number=active_message["question_number"],
    )

    score = feedback.get("score", 7)
    strengths = feedback.get("strengths", "Clear articulation.")
    improvements = feedback.get("improvements", "Include more technical metrics.")
    sample_answer = feedback.get("sample_answer", "")

    # 2. Persist answer & coaching feedback into INTERVIEW_MESSAGES
    db.execute(
        "UPDATE INTERVIEW_MESSAGES "
        "SET student_answer = :ans, score = :sc, strengths = :str, "
        "    improvements = :imp, sample_answer = :smp "
        "WHERE message_id = :mid",
        {
            "ans": student_answer,
            "sc": score,
            "str": strengths,
            "imp": improvements,
            "smp": sample_answer,
            "mid": active_message["message_id"],
        },
    )

    current_q_num = active_message["question_number"]
    total_q = session_row["total_questions"]

    # 3. Check if all questions have been answered
    if current_q_num >= total_q:
        # Wrap up interview session
        all_messages = db.query_all(
            "SELECT question_number, question_text, student_answer, score "
            "FROM INTERVIEW_MESSAGES WHERE session_id = :sid ORDER BY question_number ASC",
            {"sid": session_id},
        )

        transcript_lines = []
        for m in all_messages:
            transcript_lines.append(f"Question {m['question_number']}: {m['question_text']}")
            transcript_lines.append(f"Candidate Answer: {m['student_answer']}")
            transcript_lines.append(f"Score: {m['score']}/10\n")
        transcript_text = "\n".join(transcript_lines)

        summary_data = ai_service.summarize_mock_interview_session(
            transcript=transcript_text,
            role_title=role_title,
            company=company,
            interview_type=session_row["interview_type"],
            total_questions=total_q,
        )

        overall_score = summary_data.get("overall_score", 7.5)
        overall_feedback_json = json.dumps(summary_data)

        db.execute(
            "UPDATE INTERVIEW_SESSIONS "
            "SET status = 'completed', overall_score = :oscore, overall_feedback = :ofeedback, "
            "    updated_at = CURRENT_TIMESTAMP "
            "WHERE session_id = :sid",
            {
                "oscore": overall_score,
                "ofeedback": overall_feedback_json,
                "sid": session_id,
            },
        )

        summary_url = url_for("interview.session_summary", session_id=session_id)
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({
                "status": "completed",
                "feedback": feedback,
                "redirect_url": summary_url,
                "is_last_question": True,
            })
        flash("Interview completed! Your comprehensive coaching report is ready.", "success")
        return redirect(summary_url)

    # 4. Generate next question
    next_q_num = current_q_num + 1
    previous_messages = db.query_all(
        "SELECT question_text FROM INTERVIEW_MESSAGES WHERE session_id = :sid",
        {"sid": session_id},
    )
    previous_questions = [p["question_text"] for p in previous_messages]

    student_profile = _get_student_full_profile(user_id)
    job_info = {"title": role_title, "company": company}

    next_q_data = ai_service.generate_mock_interview_question(
        job_info=job_info,
        student_profile=student_profile,
        question_number=next_q_num,
        total_questions=total_q,
        interview_type=session_row["interview_type"],
        previous_questions=previous_questions,
    )

    next_q_text = next_q_data.get("question_text", f"Question {next_q_num}: Can you elaborate on your experience?")

    # Save next question into database
    db.execute(
        "INSERT INTO INTERVIEW_MESSAGES (session_id, question_number, question_text) "
        "VALUES (:sid, :qnum, :qtext)",
        {
            "sid": session_id,
            "qnum": next_q_num,
            "qtext": next_q_text,
        },
    )

    # Update session progress
    db.execute(
        "UPDATE INTERVIEW_SESSIONS "
        "SET current_question_index = :curr, updated_at = CURRENT_TIMESTAMP "
        "WHERE session_id = :sid",
        {"curr": next_q_num, "sid": session_id},
    )

    session_url = url_for("interview.session_view", session_id=session_id)
    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({
            "status": "next_question",
            "feedback": feedback,
            "next_question_number": next_q_num,
            "next_question_text": next_q_text,
            "redirect_url": session_url,
            "is_last_question": False,
        })

    flash(f"Answer recorded! Here is Question {next_q_num}.", "success")
    return redirect(session_url)


@interview_bp.route("/session/<int:session_id>/summary")
@login_required
def session_summary(session_id: int):
    """View detailed final report, overall score, key takeaways, and answer-by-answer breakdown."""
    user = get_current_user()
    user_id = user["user_id"]

    session_row = db.query_one(
        "SELECT * FROM INTERVIEW_SESSIONS WHERE session_id = :sid AND user_id = :uid",
        {"sid": session_id, "uid": user_id},
    )
    if not session_row:
        flash("Interview session not found.", "warning")
        return redirect(url_for("interview.index"))

    # Parse stored overall feedback JSON
    summary_data = {}
    if session_row.get("overall_feedback"):
        try:
            summary_data = json.loads(session_row["overall_feedback"])
        except Exception:
            summary_data = {"verdict": session_row["overall_feedback"]}

    # Job details
    job_row = None
    if session_row.get("job_id"):
        job_row = db.query_one("SELECT * FROM JOBS WHERE job_id = :jid", {"jid": session_row["job_id"]})

    # All Q&A turns
    messages = db.query_all(
        "SELECT * FROM INTERVIEW_MESSAGES WHERE session_id = :sid ORDER BY question_number ASC",
        {"sid": session_id},
    )

    # Calculate score metrics
    scores = [m["score"] for m in messages if m.get("score") is not None]
    avg_score = round(sum(scores) / len(scores), 1) if scores else (session_row.get("overall_score") or 0.0)

    return render_template(
        "interview/summary.html",
        interview_session=session_row,
        summary=summary_data,
        job=job_row,
        messages=messages,
        avg_score=avg_score,
    )


@interview_bp.route("/session/<int:session_id>/abandon", methods=["POST"])
@login_required
def abandon_session(session_id: int):
    """Abandon or delete an active interview session."""
    user = get_current_user()
    user_id = user["user_id"]

    db.execute(
        "DELETE FROM INTERVIEW_SESSIONS WHERE session_id = :sid AND user_id = :uid",
        {"sid": session_id, "uid": user_id},
    )
    flash("Interview session removed.", "info")
    return redirect(url_for("interview.index"))
