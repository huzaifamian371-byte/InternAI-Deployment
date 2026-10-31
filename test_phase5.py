import json
import os
import unittest
import uuid
import datetime
from app import create_app
import db
from app.services import ai_service
from app.blueprints.tracker.routes import _calculate_deadline_meta


class Phase5TestCase(unittest.TestCase):
    test_db_path = None

    def setUp(self):
        os.environ["FLASK_ENV"] = "testing"
        self.app = create_app("testing")
        self.test_db_path = self.app.config["SQLITE_DB_PATH"]
        self.client = self.app.test_client()
        self.app_context = self.app.app_context()
        self.app_context.push()

        from seed import seed_database
        seed_database(self.app.config)

    def tearDown(self):
        self.app_context.pop()
        if self.test_db_path and os.path.exists(self.test_db_path):
            try:
                os.remove(self.test_db_path)
            except Exception:
                pass

    def _register_and_login(self, email=None):
        self.client.get("/auth/logout", follow_redirects=True)
        test_email = email or f"applicant_{uuid.uuid4().hex[:8]}@internai.local"
        self.client.post(
            "/auth/register",
            data={
                "full_name": "Jordan Lee",
                "email": test_email,
                "password": "Password123#",
                "confirm_password": "Password123#",
            },
            follow_redirects=True,
        )
        user = db.query_one("SELECT user_id FROM USERS WHERE email = :email", {"email": test_email})
        # Add profile info & skills
        db.execute(
            "UPDATE STUDENT_PROFILES "
            "SET university = 'UC Berkeley', degree = 'B.S. Electrical Eng & Computer Science', graduation_year = 2026, gpa = 3.9 "
            "WHERE user_id = :uid",
            {"uid": user["user_id"]},
        )
        # Add Python skill
        python_skill = db.query_one("SELECT skill_id FROM SKILLS WHERE name = 'Python'")
        if python_skill:
            existing_skill = db.query_one(
                "SELECT student_skill_id FROM STUDENT_SKILLS WHERE user_id = :uid AND skill_id = :sid",
                {"uid": user["user_id"], "sid": python_skill["skill_id"]},
            )
            if not existing_skill:
                db.execute(
                    "INSERT INTO STUDENT_SKILLS (user_id, skill_id, proficiency) VALUES (:uid, :sid, 'Advanced')",
                    {"uid": user["user_id"], "sid": python_skill["skill_id"]},
                )
        return user["user_id"]

    def _create_sample_job(self, user_id):
        job_id = db.execute_returning_id(
            "INSERT INTO JOBS (user_id, title, company, location, work_type, raw_text, role_summary) "
            "VALUES (:uid, 'Platform Engineering Intern', 'Cloudflare', 'Austin, TX', 'hybrid', "
            "'Build distributed systems using Python and Docker.', 'Platform Engineering Intern at Cloudflare')",
            {"uid": user_id},
            id_column="job_id",
        )
        return job_id

    # =========================================================================
    # 1. AI SERVICE TESTS (MOCK INTERVIEW)
    # =========================================================================

    def test_generate_mock_interview_question_heuristic(self):
        """Test question generation for technical, behavioral, and mixed interview types."""
        job_info = {"title": "Distributed Systems Intern", "company": "Cloudflare"}
        profile = {"skills": [{"skill_name": "Python"}, {"skill_name": "Docker"}], "projects": []}

        # Test question 1
        q1 = ai_service.generate_mock_interview_question(
            job_info=job_info,
            student_profile=profile,
            question_number=1,
            total_questions=5,
            interview_type="technical",
            previous_questions=[],
        )
        self.assertIn("question_text", q1)
        self.assertTrue(len(q1["question_text"]) > 15)

        # Test that question 2 avoids repeating question 1
        q2 = ai_service.generate_mock_interview_question(
            job_info=job_info,
            student_profile=profile,
            question_number=2,
            total_questions=5,
            interview_type="technical",
            previous_questions=[q1["question_text"]],
        )
        self.assertNotEqual(q1["question_text"], q2["question_text"])

    def test_evaluate_mock_interview_answer_heuristic(self):
        """Test coaching evaluation and scoring of a student's answer."""
        eval_result = ai_service.evaluate_mock_interview_answer(
            question_text="How do you handle error propagation in distributed backend services?",
            student_answer="In my project, I implemented custom exception hierarchies and structured logging with correlation IDs. We monitored error rates using Prometheus and reduced unhandled API exceptions by 45%.",
            role_title="Backend Intern",
            company="Stripe",
            interview_type="technical",
            question_number=1,
        )
        self.assertIn("score", eval_result)
        self.assertTrue(1 <= eval_result["score"] <= 10)
        self.assertIn("strengths", eval_result)
        self.assertIn("improvements", eval_result)
        self.assertIn("sample_answer", eval_result)
        self.assertTrue(len(eval_result["sample_answer"]) > 20)

    def test_summarize_mock_interview_session_heuristic(self):
        """Test final session summary and readiness score generation."""
        transcript = (
            "Question 1: Explain caching strategies.\n"
            "Candidate Answer: I used Redis to cache read-heavy database queries.\n"
            "Score: 8/10\n\n"
            "Question 2: Tell me about a team conflict.\n"
            "Candidate Answer: We disagreed on database schema, so we ran benchmarks.\n"
            "Score: 7/10\n"
        )
        summary = ai_service.summarize_mock_interview_session(
            transcript=transcript,
            role_title="Backend Intern",
            company="Stripe",
            interview_type="mixed",
            total_questions=2,
        )
        self.assertIn("overall_score", summary)
        self.assertIn("verdict", summary)
        self.assertIn("top_improvement_areas", summary)
        self.assertIn("key_strengths", summary)
        self.assertTrue(len(summary["top_improvement_areas"]) >= 1)

    # =========================================================================
    # 2. WEB WORKFLOW TESTS (MOCK INTERVIEW BLUEPRINT)
    # =========================================================================

    def test_interview_lobby_auth_required(self):
        """Ensure unauthenticated access to /interview redirects to login."""
        resp = self.client.get("/interview/", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/auth/login", resp.headers["Location"])

    def test_interview_lobby_view(self):
        """Verify lobby page renders successfully with analyzed jobs and active sessions."""
        uid = self._register_and_login()
        jid = self._create_sample_job(uid)

        resp = self.client.get("/interview/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"AI Mock Interview", resp.data)
        self.assertIn(b"Cloudflare", resp.data)

    def test_start_interview_with_analyzed_job(self):
        """Test starting an interview session linked to an existing analyzed job."""
        uid = self._register_and_login()
        jid = self._create_sample_job(uid)

        resp = self.client.post(
            "/interview/start",
            data={
                "job_id": str(jid),
                "interview_type": "technical",
                "total_questions": "3",
            },
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Mock Interview Session", resp.data)
        self.assertIn(b"Question 1 of 3", resp.data)

        # Check DB records
        sess = db.query_one(
            "SELECT * FROM INTERVIEW_SESSIONS WHERE user_id = :uid AND status = 'in_progress'",
            {"uid": uid},
        )
        self.assertIsNotNone(sess)
        self.assertEqual(sess["job_id"], jid)
        self.assertEqual(sess["interview_type"], "technical")
        self.assertEqual(sess["total_questions"], 3)

        # Check first message created
        msg1 = db.query_one(
            "SELECT * FROM INTERVIEW_MESSAGES WHERE session_id = :sid AND question_number = 1",
            {"sid": sess["session_id"]},
        )
        self.assertIsNotNone(msg1)
        self.assertIsNotNone(msg1["question_text"])
        self.assertIsNone(msg1["student_answer"])

    def test_start_interview_with_custom_role(self):
        """Test starting an interview session with a custom entered position."""
        uid = self._register_and_login()

        resp = self.client.post(
            "/interview/start",
            data={
                "job_id": "custom",
                "custom_role": "AI Research Intern",
                "custom_company": "Anthropic",
                "custom_desc": "Work on RLHF and LLM evaluation benchmarks.",
                "interview_type": "mixed",
                "total_questions": "3",
            },
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"AI Research Intern", resp.data)

    def test_submit_answers_and_reach_summary(self):
        """Test full turn-by-turn answer submission leading to session completion and summary report."""
        uid = self._register_and_login()
        jid = self._create_sample_job(uid)

        # Start 3-question interview
        self.client.post(
            "/interview/start",
            data={"job_id": str(jid), "interview_type": "mixed", "total_questions": "3"},
            follow_redirects=True,
        )
        sess = db.query_one("SELECT * FROM INTERVIEW_SESSIONS WHERE user_id = :uid", {"uid": uid})
        sid = sess["session_id"]

        # Answer Question 1
        resp1 = self.client.post(
            f"/interview/session/{sid}/answer",
            data={"student_answer": "I built a Python microservice with asynchronous tasks in Celery and Redis to handle peak query load."},
            follow_redirects=True,
        )
        self.assertEqual(resp1.status_code, 200)
        # Should now be on question 2
        self.assertIn(b"Question 2", resp1.data)

        # Answer Question 2
        resp2 = self.client.post(
            f"/interview/session/{sid}/answer",
            data={"student_answer": "When our team faced a tight milestone, I organized a sprint backlog review and helped teammates unblock API integrations."},
            follow_redirects=True,
        )
        self.assertEqual(resp2.status_code, 200)
        self.assertIn(b"Question 3", resp2.data)

        # Answer Question 3 (Final question)
        resp3 = self.client.post(
            f"/interview/session/{sid}/answer",
            data={"student_answer": "I am excited to contribute my distributed systems experience and learn from senior engineers at Cloudflare."},
            follow_redirects=True,
        )
        self.assertEqual(resp3.status_code, 200)
        # Should redirect to summary report
        self.assertIn(b"Mock Interview Performance Report", resp3.data)
        self.assertIn(b"Overall Readiness Assessment", resp3.data)

        # Confirm DB session marked completed
        updated_sess = db.query_one("SELECT * FROM INTERVIEW_SESSIONS WHERE session_id = :sid", {"sid": sid})
        self.assertEqual(updated_sess["status"], "completed")
        self.assertIsNotNone(updated_sess["overall_score"])
        self.assertIsNotNone(updated_sess["overall_feedback"])

    def test_abandon_interview_session(self):
        """Test abandoning an in-progress session removes it cleanly."""
        uid = self._register_and_login()
        jid = self._create_sample_job(uid)

        self.client.post(
            "/interview/start",
            data={"job_id": str(jid), "interview_type": "behavioral", "total_questions": "3"},
            follow_redirects=True,
        )
        sess = db.query_one("SELECT * FROM INTERVIEW_SESSIONS WHERE user_id = :uid", {"uid": uid})
        sid = sess["session_id"]

        resp = self.client.post(f"/interview/session/{sid}/abandon", follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Interview session removed", resp.data)

        deleted = db.query_one("SELECT * FROM INTERVIEW_SESSIONS WHERE session_id = :sid", {"sid": sid})
        self.assertIsNone(deleted)

    # =========================================================================
    # 3. INTERNSHIP TRACKER (KANBAN PIPELINE) TESTS
    # =========================================================================

    def test_tracker_index_auth_required(self):
        """Ensure unauthenticated access to /tracker redirects to login."""
        resp = self.client.get("/tracker/", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/auth/login", resp.headers["Location"])

    def test_tracker_index_view(self):
        """Ensure Kanban board renders with all 5 columns."""
        self._register_and_login()
        resp = self.client.get("/tracker/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Application Pipeline Tracker", resp.data)
        self.assertIn(b"Saved", resp.data)
        self.assertIn(b"Applied", resp.data)
        self.assertIn(b"Interview", resp.data)
        self.assertIn(b"Offer", resp.data)
        self.assertIn(b"Closed", resp.data)

    def test_add_and_drag_drop_update_application(self):
        """Test creating an application and updating its status via drag-and-drop AJAX endpoint."""
        uid = self._register_and_login()

        # 1. Add application
        resp = self.client.post(
            "/tracker/add",
            data={
                "company_name": "Datadog",
                "role_title": "Site Reliability Engineering Intern",
                "status": "Saved",
                "application_date": "2026-10-10",
                "deadline": "2026-11-01",
                "notes": "Met recruiter at university career fair.",
            },
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Datadog", resp.data)

        app_row = db.query_one(
            "SELECT * FROM APPLICATIONS WHERE user_id = :uid AND company_name = 'Datadog'",
            {"uid": uid},
        )
        self.assertIsNotNone(app_row)
        self.assertEqual(app_row["status"], "Saved")
        app_id = app_row["application_id"]

        # 2. Simulate Drag-and-Drop update to 'Applied'
        ajax_resp = self.client.post(
            f"/tracker/update-status/{app_id}",
            json={"status": "Applied"},
        )
        self.assertEqual(ajax_resp.status_code, 200)
        data = ajax_resp.get_json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("new_status"), "Applied")

        # Verify DB updated
        updated = db.query_one("SELECT status FROM APPLICATIONS WHERE application_id = :aid", {"aid": app_id})
        self.assertEqual(updated["status"], "Applied")

        # 3. Simulate Drag-and-Drop update to 'Interview'
        ajax_resp2 = self.client.post(
            f"/tracker/update-status/{app_id}",
            json={"status": "Interview"},
        )
        self.assertEqual(ajax_resp2.status_code, 200)
        updated2 = db.query_one("SELECT status FROM APPLICATIONS WHERE application_id = :aid", {"aid": app_id})
        self.assertEqual(updated2["status"], "Interview")

    def test_get_and_edit_application(self):
        """Test retrieving application details JSON and editing values."""
        uid = self._register_and_login()

        app_id = db.execute_returning_id(
            "INSERT INTO APPLICATIONS (user_id, company_name, role_title, status, notes) "
            "VALUES (:uid, 'Figma', 'Product Design Intern', 'Saved', 'Portfolio review needed')",
            {"uid": uid},
            id_column="application_id",
        )

        # GET application JSON for edit modal
        resp = self.client.get(f"/tracker/application/{app_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["company_name"], "Figma")
        self.assertEqual(data["notes"], "Portfolio review needed")

        # POST update
        edit_resp = self.client.post(
            f"/tracker/update/{app_id}",
            data={
                "company_name": "Figma",
                "role_title": "Design Systems Intern",
                "status": "Applied",
                "notes": "Portfolio submitted, referral requested.",
            },
            follow_redirects=True,
        )
        self.assertEqual(edit_resp.status_code, 200)
        self.assertIn(b"Application updated successfully", edit_resp.data)

        updated = db.query_one("SELECT * FROM APPLICATIONS WHERE application_id = :aid", {"aid": app_id})
        self.assertEqual(updated["role_title"], "Design Systems Intern")
        self.assertEqual(updated["status"], "Applied")

    def test_delete_application(self):
        """Test deleting an application from the tracker."""
        uid = self._register_and_login()

        app_id = db.execute_returning_id(
            "INSERT INTO APPLICATIONS (user_id, company_name, role_title, status) "
            "VALUES (:uid, 'Notion', 'Mobile Intern', 'Saved')",
            {"uid": uid},
            id_column="application_id",
        )

        del_resp = self.client.post(f"/tracker/delete/{app_id}", follow_redirects=True)
        self.assertEqual(del_resp.status_code, 200)
        self.assertIn(b"Application removed from tracker", del_resp.data)

        check = db.query_one("SELECT * FROM APPLICATIONS WHERE application_id = :aid", {"aid": app_id})
        self.assertIsNone(check)

    def test_quick_add_from_job(self):
        """Test one-click adding an analyzed job into tracker."""
        uid = self._register_and_login()
        jid = self._create_sample_job(uid)

        resp = self.client.post(
            "/tracker/quick-add",
            data={"job_id": str(jid)},
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"added to Saved applications", resp.data)

        app_row = db.query_one(
            "SELECT * FROM APPLICATIONS WHERE user_id = :uid AND job_id = :jid",
            {"uid": uid, "jid": jid},
        )
        self.assertIsNotNone(app_row)
        self.assertEqual(app_row["company_name"], "Cloudflare")
        self.assertEqual(app_row["status"], "Saved")

    def test_deadline_calculation_helper(self):
        """Test deadline urgency and badge calculation."""
        today = datetime.date.today()
        yesterday_str = (today - datetime.timedelta(days=2)).isoformat()
        soon_str = (today + datetime.timedelta(days=2)).isoformat()
        future_str = (today + datetime.timedelta(days=20)).isoformat()

        # Overdue
        meta_overdue = _calculate_deadline_meta(yesterday_str, "Applied")
        self.assertEqual(meta_overdue["badge"], "danger")
        self.assertIn("Overdue", meta_overdue["label"])

        # Due Soon
        meta_soon = _calculate_deadline_meta(soon_str, "Saved")
        self.assertEqual(meta_soon["badge"], "warning")
        self.assertIn("Due in 2d", meta_soon["label"])

        # Future
        meta_future = _calculate_deadline_meta(future_str, "Applied")
        self.assertEqual(meta_future["badge"], "default")

        # When already Offered/Closed, not treated as urgent overdue
        meta_offered = _calculate_deadline_meta(yesterday_str, "Offer")
        self.assertEqual(meta_offered["badge"], "neutral")

    def test_user_data_isolation(self):
        """Ensure candidate A cannot view, update, or delete candidate B's data."""
        uid_a = self._register_and_login(f"candidate_a_{uuid.uuid4().hex[:6]}@internai.local")
        app_a = db.execute_returning_id(
            "INSERT INTO APPLICATIONS (user_id, company_name, role_title, status) "
            "VALUES (:uid, 'Apple', 'iOS Intern', 'Saved')",
            {"uid": uid_a},
            id_column="application_id",
        )

        # Login as candidate B
        self._register_and_login(f"candidate_b_{uuid.uuid4().hex[:6]}@internai.local")

        # Candidate B attempts to delete Candidate A's application
        self.client.post(f"/tracker/delete/{app_a}", follow_redirects=True)
        # Verify app_a is NOT deleted
        check_a = db.query_one("SELECT * FROM APPLICATIONS WHERE application_id = :aid", {"aid": app_a})
        self.assertIsNotNone(check_a)

        # Candidate B attempts to update Candidate A's status
        resp = self.client.post(f"/tracker/update-status/{app_a}", json={"status": "Offer"})
        self.assertEqual(resp.status_code, 404)
        check_a_status = db.query_one("SELECT status FROM APPLICATIONS WHERE application_id = :aid", {"aid": app_a})
        self.assertEqual(check_a_status["status"], "Saved")


if __name__ == "__main__":
    unittest.main()
