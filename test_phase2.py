import os
import unittest
from app import create_app
import db
from app.services import ai_service


class Phase2TestCase(unittest.TestCase):
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
        # Clear existing session / logout to prevent redirect on /auth/register
        self.client.get("/auth/logout", follow_redirects=True)
        test_email = email or f"tester_{uuid.uuid4().hex[:8]}@internai.local"
        self.client.post(
            "/auth/register",
            data={
                "full_name": "Test Student",
                "email": test_email,
                "password": "Password123#",
                "confirm_password": "Password123#",
            },
            follow_redirects=True,
        )
        user = db.query_one("SELECT user_id FROM USERS WHERE email = :email", {"email": test_email})
        return user["user_id"]

    def test_ai_service_analyze_job_description(self):
        """Test AI service parses raw job description text into structured dictionary."""
        sample_text = """Software Engineer Intern
Datadog — New York, NY (Hybrid)
Compensation: $50 / hour
Duration: 12 weeks
Requirements:
- Strong proficiency in Python or Go.
- Working knowledge of SQL, PostgreSQL, and Git.
Preferred:
- Experience with Docker and AWS.
"""
        result = ai_service.analyze_job_description(sample_text)
        self.assertIsInstance(result, dict)
        self.assertIn("job_title", result)
        self.assertIn("company", result)
        self.assertIn("required_skills", result)
        self.assertIn("preferred_skills", result)
        self.assertIn("work_type", result)

        self.assertIn(result["work_type"], ("onsite", "remote", "hybrid", "unspecified"))
        self.assertTrue(len(result["required_skills"]) > 0)

    def test_ai_service_semantic_skill_matching(self):
        """Test semantic skill matching and explainable weighted percentage formula."""
        student_skills = [
            {"skill_name": "Python", "proficiency": "Advanced"},
            {"skill_name": "Postgres", "proficiency": "Intermediate"},  # Semantic alias for PostgreSQL
            {"skill_name": "Git", "proficiency": "Intermediate"},
        ]
        required_skills = ["Python", "PostgreSQL", "Go"]
        preferred_skills = ["Docker", "AWS"]

        match_res = ai_service.match_skills(student_skills, required_skills, preferred_skills)

        self.assertIn("matched_skills", match_res)
        self.assertIn("missing_skills", match_res)
        self.assertIn("match_percentage", match_res)
        self.assertIn("areas_to_improve", match_res)

        # Check that 'Python' is matched
        matched_reqs = [m["requirement"] for m in match_res["matched_skills"]]
        self.assertIn("Python", matched_reqs)

        # Check semantic alias: 'Postgres' should match 'PostgreSQL'
        self.assertIn("PostgreSQL", matched_reqs)

        # 'Go' should be in missing_skills with critical importance
        missing_crit = [m["requirement"] for m in match_res["missing_skills"] if m.get("importance") == "critical"]
        self.assertIn("Go", missing_crit)

        # Score must be between 0.0 and 100.0
        pct = match_res["match_percentage"]
        self.assertTrue(0.0 <= pct <= 100.0)

    def test_job_analyzer_end_to_end_flow(self):
        """Test posting a job, storing parsed requirements, and viewing details."""
        uid = self._register_and_login()

        job_posting_text = """Backend Engineering Intern
Stripe — Seattle, WA (Remote)
Compensation: $55/hr
Duration: 12-week summer 2026 internship
Responsibilities: Build distributed billing pipelines.
Requirements:
- Python programming experience
- Experience with REST APIs and SQL
Preferred:
- Redis and Docker knowledge
"""

        # 1. Post job description
        parse_res = self.client.post(
            "/analyzer/parse",
            data={"raw_text": job_posting_text},
            follow_redirects=True,
        )
        self.assertEqual(parse_res.status_code, 200)

        # 2. Verify stored in JOBS table
        job = db.query_one("SELECT * FROM JOBS WHERE user_id = :uid", {"uid": uid})
        self.assertIsNotNone(job)
        self.assertEqual(job["work_type"], "remote")

        # 3. Verify requirements stored in JOB_REQUIREMENTS
        reqs = db.query_all("SELECT * FROM JOB_REQUIREMENTS WHERE job_id = :jid", {"jid": job["job_id"]})
        self.assertTrue(len(reqs) >= 2)

        # 4. Verify analysis stored in JOB_ANALYSIS
        analysis = db.query_one("SELECT * FROM JOB_ANALYSIS WHERE job_id = :jid", {"jid": job["job_id"]})
        self.assertIsNotNone(analysis)
        self.assertIsNotNone(analysis["structured_json"])

        # 5. Access view endpoint
        view_res = self.client.get(f"/analyzer/{job['job_id']}")
        self.assertEqual(view_res.status_code, 200)
        self.assertIn(b"Mandatory Technical Requirements", view_res.data)

    def test_skill_matcher_end_to_end_flow(self):
        """Test running match against a job and recalculating match score."""
        uid = self._register_and_login()

        # Add profile skill
        self.client.post(
            "/profile/skills/add",
            data={"skill_name": "Python", "proficiency": "Advanced", "category": "programming"},
            follow_redirects=True,
        )

        # Create a job
        job_id = db.execute_returning_id(
            "INSERT INTO JOBS (user_id, title, company, location, work_type, raw_text) "
            "VALUES (:uid, :title, :co, :loc, :wt, :raw)",
            {
                "uid": uid,
                "title": "Data Engineering Intern",
                "co": "Spotify",
                "loc": "Boston, MA",
                "wt": "hybrid",
                "raw": "Data engineering with Python and SQL",
            },
            id_column="job_id",
        )
        db.execute(
            "INSERT INTO JOB_REQUIREMENTS (job_id, skill_name, req_type, importance) "
            "VALUES (:jid, 'Python', 'required', 'critical')",
            {"jid": job_id},
        )
        db.execute(
            "INSERT INTO JOB_REQUIREMENTS (job_id, skill_name, req_type, importance) "
            "VALUES (:jid, 'SQL', 'required', 'critical')",
            {"jid": job_id},
        )
        db.execute(
            "INSERT INTO JOB_ANALYSIS (job_id, user_id) VALUES (:jid, :uid)",
            {"jid": job_id, "uid": uid},
        )

        # View match page (triggers calculation)
        match_view = self.client.get(f"/matcher/{job_id}")
        self.assertEqual(match_view.status_code, 200)
        self.assertIn(b"Skill Match Breakdown", match_view.data)
        self.assertIn(b"Transparent & Explainable Scoring Model", match_view.data)

        # Verify score updated in database
        analysis = db.query_one("SELECT match_percentage FROM JOB_ANALYSIS WHERE job_id = :jid", {"jid": job_id})
        self.assertIsNotNone(analysis["match_percentage"])

        # Test recalculate endpoint
        recalc_res = self.client.post(f"/matcher/{job_id}/recalculate", follow_redirects=True)
        self.assertEqual(recalc_res.status_code, 200)


if __name__ == "__main__":
    unittest.main()
