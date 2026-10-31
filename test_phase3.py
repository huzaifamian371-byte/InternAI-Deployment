import json
import os
import unittest
from app import create_app
import db
from app.services import ai_service


class Phase3TestCase(unittest.TestCase):
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
        import uuid
        self.client.get("/auth/logout", follow_redirects=True)
        test_email = email or f"student_{uuid.uuid4().hex[:8]}@internai.local"
        self.client.post(
            "/auth/register",
            data={
                "full_name": "Roadmap Student",
                "email": test_email,
                "password": "Password123#",
                "confirm_password": "Password123#",
            },
            follow_redirects=True,
        )
        user = db.query_one("SELECT user_id FROM USERS WHERE email = :email", {"email": test_email})
        return user["user_id"]

    def _create_sample_job(self, user_id, raw_text=None, title="Software Engineering Intern"):
        text = raw_text or """Software Engineering Intern
TechCorp — Seattle, WA (Hybrid)
Duration: 10 weeks
Requirements:
- Strong knowledge of Python and FastAPI
- Familiarity with PostgreSQL and Docker
- Understanding of CI/CD pipelines
"""
        job_id = db.execute_returning_id(
            "INSERT INTO JOBS (user_id, title, company, location, work_type, raw_text) "
            "VALUES (:uid, :title, 'TechCorp', 'Seattle, WA', 'hybrid', :raw)",
            {"uid": user_id, "title": title, "raw": text},
            id_column="job_id",
        )
        db.execute(
            "INSERT INTO JOB_ANALYSIS (job_id, user_id, match_percentage, risk_level, risk_score) "
            "VALUES (:jid, :uid, 50.0, 'Safe', 0.5)",
            {"jid": job_id, "uid": user_id},
        )
        db.execute(
            "INSERT INTO JOB_REQUIREMENTS (job_id, skill_name, req_type, importance) "
            "VALUES (:jid, 'FastAPI', 'required', 'critical')",
            {"jid": job_id},
        )
        db.execute(
            "INSERT INTO JOB_REQUIREMENTS (job_id, skill_name, req_type, importance) "
            "VALUES (:jid, 'Docker', 'preferred', 'nice_to_have')",
            {"jid": job_id},
        )
        return job_id

    # ---------------------------------------------------------
    # 1. AI SERVICE ROADMAP & RISK TESTS
    # ---------------------------------------------------------

    def test_ai_service_generate_learning_roadmap(self):
        """Test generating structured week-by-week learning roadmap with milestones."""
        missing = [
            {"requirement": "FastAPI", "importance": "critical", "reason": "Not found in profile"},
            {"requirement": "Docker", "importance": "nice_to_have", "reason": "Not found in profile"},
        ]
        roadmap = ai_service.generate_learning_roadmap(
            job_title="Backend Intern",
            missing_skills=missing,
            total_weeks=4,
            hours_per_week=10,
        )

        self.assertIsInstance(roadmap, dict)
        self.assertIn("weeks", roadmap)
        self.assertIn("practice_projects", roadmap)
        self.assertEqual(len(roadmap["weeks"]), 4)

        first_week = roadmap["weeks"][0]
        self.assertIn("week_number", first_week)
        self.assertIn("theme", first_week)
        self.assertIn("weekly_focus", first_week)
        self.assertTrue(len(first_week["topics"]) > 0)

        first_topic = first_week["topics"][0]
        self.assertIn("topic_id", first_topic)
        self.assertIn("title", first_topic)
        self.assertIn("resource_type", first_topic)

        # Check practice projects
        self.assertTrue(len(roadmap["practice_projects"]) >= 1)
        project = roadmap["practice_projects"][0]
        self.assertIn("title", project)
        self.assertIn("description", project)
        self.assertIn("technologies", project)

    def test_ai_service_analyze_job_risk_clean_posting(self):
        """Test risk analyzer on a legitimate job posting returns Safe / Low score."""
        legit_text = """Backend Engineer Intern
Stripe — San Francisco, CA (Hybrid)
We are seeking an undergraduate intern for summer 2027.
Requirements:
- Enrolled in CS or related degree
- Python or Java proficiency
- Apply through our official portal at stripe.com/jobs
Equal opportunity employer. Paid internship: $48/hour.
"""
        result = ai_service.analyze_job_risk(legit_text, company_name="Stripe")
        self.assertIsInstance(result, dict)
        self.assertIn("risk_score", result)
        self.assertIn("risk_level", result)
        self.assertIn("indicators", result)
        self.assertIn("safety_advice", result)
        self.assertIn("disclaimer", result)

        self.assertIn(result["risk_level"], ("Safe", "Low"))
        self.assertLessEqual(result["risk_score"], 3.5)

    def test_ai_service_analyze_job_risk_scam_posting(self):
        """Test risk analyzer catches scam indicators: upfront fees, Telegram, check cashing, instant hire."""
        scam_text = """URGENT HIRING: Data Entry & Virtual Assistant Intern!
EARN $60/HOUR IMMEDIATE START NO EXPERIENCE NEEDED!
Send your resume and SSN directly to hr-department2026@gmail.com.
All interviews conducted exclusively via Telegram username @hr_recruiter_fast.
You will be sent a cashier check of $1,500 to purchase home office equipment.
A refundable background clearance fee of $85 must be paid via Bitcoin or Zelle before onboarding.
"""
        result = ai_service.analyze_job_risk(scam_text)
        self.assertIsInstance(result, dict)
        self.assertIn("risk_level", result)
        self.assertEqual(result["risk_level"], "High")
        self.assertGreaterEqual(result["risk_score"], 6.0)

        # Check that indicators contain quoted evidence and high severity
        severities = [ind["severity"] for ind in result["indicators"]]
        self.assertIn("High", severities)

        # Check that disclaimer is always included
        self.assertIn("not legal proof of fraud", result["disclaimer"].lower())

    # ---------------------------------------------------------
    # 2. ROADMAP BLUEPRINT ROUTES
    # ---------------------------------------------------------

    def test_roadmap_index_and_generate_flow(self):
        """Test navigating to roadmap generator, posting parameters, and viewing roadmap."""
        uid = self._register_and_login()
        jid = self._create_sample_job(uid)

        # 1. Access Roadmap Index
        res = self.client.get("/roadmap/")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"AI Learning Roadmaps", res.data)

        # 2. Access Roadmap Generate Form
        res = self.client.get(f"/roadmap/generate/{jid}")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Generate AI Learning Roadmap", res.data)

        # 3. Submit Roadmap Generation Form
        res = self.client.post(
            f"/roadmap/generate/{jid}",
            data={
                "total_weeks": "4",
                "hours_per_week": "12",
            },
            follow_redirects=True,
        )
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Week 1", res.data)
        self.assertIn(b"Curriculum Completion", res.data)

        # Verify database record
        saved_rm = db.query_one(
            "SELECT * FROM ROADMAPS WHERE user_id = :uid AND job_id = :jid",
            {"uid": uid, "jid": jid},
        )
        self.assertIsNotNone(saved_rm)
        self.assertEqual(saved_rm["total_weeks"], 4)
        self.assertEqual(saved_rm["hours_per_week"], 12)

        # 4. Toggle Topic Completion via AJAX
        roadmap_id = saved_rm["roadmap_id"]
        rm_data = json.loads(saved_rm["roadmap_data"])
        first_topic_id = rm_data["weeks"][0]["topics"][0]["topic_id"]

        ajax_res = self.client.post(
            f"/roadmap/{roadmap_id}/toggle-topic",
            json={"topic_id": first_topic_id},
        )
        self.assertEqual(ajax_res.status_code, 200)
        json_data = ajax_res.get_json()
        self.assertTrue(json_data["success"])
        self.assertTrue(json_data["is_completed"])
        self.assertEqual(json_data["completed_count"], 1)

        # Untoggle topic
        ajax_res2 = self.client.post(
            f"/roadmap/{roadmap_id}/toggle-topic",
            json={"topic_id": first_topic_id},
        )
        self.assertEqual(ajax_res2.status_code, 200)
        json_data2 = ajax_res2.get_json()
        self.assertFalse(json_data2["is_completed"])
        self.assertEqual(json_data2["completed_count"], 0)

        # 5. Delete Roadmap
        del_res = self.client.post(f"/roadmap/{roadmap_id}/delete", follow_redirects=True)
        self.assertEqual(del_res.status_code, 200)
        check_deleted = db.query_one("SELECT * FROM ROADMAPS WHERE roadmap_id = :rid", {"rid": roadmap_id})
        self.assertIsNone(check_deleted)

    # ---------------------------------------------------------
    # 3. JOB RISK ANALYZER BLUEPRINT ROUTES
    # ---------------------------------------------------------

    def test_risk_index_and_standalone_check(self):
        """Test standalone scam check with pasted text."""
        self._register_and_login()

        # 1. View risk index
        res = self.client.get("/risk/")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Job Risk & Scam Analyzer", res.data)

        # 2. Submit standalone scam posting
        res = self.client.post(
            "/risk/check",
            data={
                "job_text": "Earn $500 daily processing packages. Contact us on Telegram @paynow and wire $50 for onboarding badge."
            },
        )
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Risk Assessment", res.data)
        self.assertIn(b"Automated Risk Assessment", res.data)

    def test_job_risk_view_and_report_posting(self):
        """Test analyzing a saved job's risk and submitting a safety report to admin."""
        uid = self._register_and_login()
        jid = self._create_sample_job(
            uid,
            raw_text="Suspicious Job: Send $100 registration fee to HR via Western Union. Telegram @hr_scam.",
            title="Suspicious Remote Assistant",
        )

        # 1. View Risk analysis for job
        res = self.client.get(f"/risk/{jid}")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Risk Assessment", res.data)

        # Check DB was updated with risk level
        analysis = db.query_one("SELECT risk_level, risk_score FROM JOB_ANALYSIS WHERE job_id = :jid", {"jid": jid})
        self.assertIn(analysis["risk_level"], ("Medium", "High"))

        # 2. Report posting to admin
        rep_res = self.client.post(
            f"/risk/report/{jid}",
            data={
                "reason": "Upfront fee request and suspicious Telegram contact",
                "details": "They asked for $100 registration fee before any interview.",
            },
            follow_redirects=True,
        )
        self.assertEqual(rep_res.status_code, 200)
        self.assertIn(b"Report submitted successfully", rep_res.data)

        # Verify REPORTED_POSTINGS record in DB
        report_row = db.query_one(
            "SELECT * FROM REPORTED_POSTINGS WHERE user_id = :uid AND job_id = :jid",
            {"uid": uid, "jid": jid},
        )
        self.assertIsNotNone(report_row)
        self.assertEqual(report_row["status"], "pending")
        self.assertIn("Upfront fee", report_row["reason"])


if __name__ == "__main__":
    unittest.main()
