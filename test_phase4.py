import io
import json
import os
import unittest
from app import create_app
import db
from app.services import ai_service, cv_service


class Phase4TestCase(unittest.TestCase):
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
        test_email = email or f"applicant_{uuid.uuid4().hex[:8]}@internai.local"
        self.client.post(
            "/auth/register",
            data={
                "full_name": "Alex Morgan",
                "email": test_email,
                "password": "Password123#",
                "confirm_password": "Password123#",
            },
            follow_redirects=True,
        )
        user = db.query_one("SELECT user_id FROM USERS WHERE email = :email", {"email": test_email})
        # Add profile info
        db.execute(
            "UPDATE STUDENT_PROFILES "
            "SET university = 'Stanford University', degree = 'B.S. in Computer Science', graduation_year = 2026, gpa = 3.85 "
            "WHERE user_id = :uid",
            {"uid": user["user_id"]},
        )
        return user["user_id"]

    def _create_sample_job(self, user_id):
        job_id = db.execute_returning_id(
            "INSERT INTO JOBS (user_id, title, company, location, work_type, raw_text) "
            "VALUES (:uid, 'Backend Engineering Intern', 'Stripe', 'San Francisco, CA', 'hybrid', 'Requirements: Python, REST APIs, PostgreSQL, Docker')",
            {"uid": user_id},
            id_column="job_id",
        )
        return job_id

    # ---------------------------------------------------------
    # 1. CV SERVICE & EXTRACTION TESTS
    # ---------------------------------------------------------

    def test_cv_service_allowed_extensions(self):
        """Test file extension validation for CV documents."""
        self.assertTrue(cv_service.allowed_file("resume.pdf"))
        self.assertTrue(cv_service.allowed_file("cv_alex.docx"))
        self.assertTrue(cv_service.allowed_file("my_cv.PDF"))
        self.assertFalse(cv_service.allowed_file("malicious.exe"))
        self.assertFalse(cv_service.allowed_file("script.py"))
        self.assertFalse(cv_service.allowed_file("resume.png"))
        self.assertFalse(cv_service.allowed_file("no_extension"))

    def test_ai_service_analyze_cv_text(self):
        """Test AI CV extraction, 4-part score breakdown, and bullet rewrites."""
        cv_text = """Alex Morgan
alex.morgan@stanford.edu | (555) 234-5678 | San Francisco, CA
GitHub: github.com/alexm-dev | LinkedIn: linkedin.com/in/alexm

EDUCATION:
Stanford University — B.S. in Computer Science (Expected June 2026) | GPA: 3.88

TECHNICAL SKILLS:
Languages: Python, Go, JavaScript, TypeScript, SQL, C++
Web & Backend: FastAPI, Flask, React, Node.js, REST APIs, PostgreSQL, Redis
DevOps & Tools: Docker, Git, Linux, GitHub Actions, AWS

EXPERIENCE:
Software Engineering Intern | OpenTech Labs (Summer 2025)
- Worked on backend APIs and fixed several bugs in the application.
- Assisted with database queries and optimized performance.

PROJECTS:
Cloud Distributed Cache System:
- Engineered in-memory caching engine in Go with LRU eviction policy.
- Achieved sub-2ms lookup latency across 100k concurrent client requests.
"""
        result = ai_service.analyze_cv_text(cv_text, job_title="Backend Intern")

        self.assertIsInstance(result, dict)
        self.assertIn("score", result)
        self.assertIn("extracted_skills", result)
        self.assertIn("bullet_rewrites", result)
        self.assertIn("weak_or_missing_sections", result)

        # Check score structure
        score = result["score"]
        self.assertIn("clarity", score)
        self.assertIn("relevance", score)
        self.assertIn("impact", score)
        self.assertIn("formatting_readability", score)
        self.assertIn("total_score", score)
        self.assertGreaterEqual(score["total_score"], 50)
        self.assertLessEqual(score["total_score"], 100)

        # Check extracted skills
        skills = result["extracted_skills"]
        prog = [s.lower() for s in skills.get("programming", [])]
        self.assertIn("python", prog)

        # Check bullet rewrites
        self.assertTrue(len(result["bullet_rewrites"]) >= 1)
        rw = result["bullet_rewrites"][0]
        self.assertIn("original_bullet", rw)
        self.assertIn("rewritten_bullet", rw)
        self.assertIn("improvement_reason", rw)

    # ---------------------------------------------------------
    # 2. APPLICATION ASSISTANT AI GENERATION TESTS
    # ---------------------------------------------------------

    def test_ai_service_cover_letter_generation(self):
        """Test cover letter generation with tone selector and anti-hallucination grounding."""
        student_profile = {
            "full_name": "Alex Morgan",
            "university": "Stanford University",
            "degree": "B.S. in Computer Science",
            "skills": [{"skill_name": "Python"}, {"skill_name": "FastAPI"}, {"skill_name": "PostgreSQL"}],
            "projects": [
                {"title": "Distributed Cache", "tech_used": "Go, Redis", "description": "High-throughput memory store"}
            ],
        }
        job_info = {
            "title": "Backend Engineering Intern",
            "company": "Stripe",
            "location": "San Francisco, CA",
        }

        # Test Formal Tone
        res_formal = ai_service.generate_cover_letter(student_profile, job_info, tone="formal")
        self.assertIn("body_paragraphs", res_formal)
        self.assertIn("Alex Morgan", res_formal["candidate_name"])
        self.assertTrue(len(res_formal["body_paragraphs"]) >= 3)

        # Test Friendly Tone
        res_friendly = ai_service.generate_cover_letter(student_profile, job_info, tone="friendly")
        self.assertIn("body_paragraphs", res_friendly)

        # Test Confident Tone
        res_confident = ai_service.generate_cover_letter(student_profile, job_info, tone="confident")
        self.assertIn("body_paragraphs", res_confident)

    def test_ai_service_application_email_generation(self):
        """Test outreach email generation with subject line and call to action."""
        student_profile = {
            "full_name": "Alex Morgan",
            "university": "Stanford University",
            "degree": "B.S. in Computer Science",
            "skills": [{"skill_name": "Python"}, {"skill_name": "Docker"}],
        }
        job_info = {"title": "Software Engineering Intern", "company": "Datadog"}

        res = ai_service.generate_application_email(student_profile, job_info, tone="confident")
        self.assertIn("subject_line", res)
        self.assertIn("email_body", res)
        self.assertIn("call_to_action", res)
        self.assertIn("Alex Morgan", res["candidate_name"])

    def test_ai_service_interview_prep_generation(self):
        """Test tailored practice questions and STAR frameworks."""
        student_profile = {
            "full_name": "Alex Morgan",
            "skills": [{"skill_name": "Python"}, {"skill_name": "PostgreSQL"}],
            "projects": [{"title": "Web API Service", "tech_used": "Python, SQL"}],
        }
        job_info = {"title": "Backend Intern", "company": "Stripe"}

        res = ai_service.generate_interview_prep(student_profile, job_info)
        self.assertIn("questions", res)
        self.assertIn("top_preparation_tips", res)
        self.assertTrue(len(res["questions"]) >= 3)

        first_q = res["questions"][0]
        self.assertIn("question", first_q)
        self.assertIn("why_it_matters", first_q)
        self.assertIn("talking_points", first_q)
        self.assertIn("sample_framework", first_q)

    # ---------------------------------------------------------
    # 3. CV BLUEPRINT ROUTES & SKILL IMPORT FLOW
    # ---------------------------------------------------------

    def test_cv_dashboard_and_pasting_flow(self):
        """Test viewing CV portal, pasting CV text, viewing report, and importing skills."""
        uid = self._register_and_login()
        jid = self._create_sample_job(uid)

        # 1. Access CV Dashboard
        res = self.client.get("/cv/")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"AI CV & Resume Analyzer", res.data)

        # 2. Paste CV Text
        sample_cv = """Candidate Name: Alex Morgan
Email: alex@example.edu
Stanford University - BS Computer Science (2026)
Skills: Python, FastAPI, Docker, PostgreSQL, Redis, Git
Project: REST API with 10k daily users. Built with Python and PostgreSQL.
"""
        post_res = self.client.post(
            "/cv/paste",
            data={
                "cv_text": sample_cv,
                "job_id": str(jid),
            },
            follow_redirects=True,
        )
        self.assertEqual(post_res.status_code, 200)
        self.assertIn(b"Resume Evaluation", post_res.data)
        self.assertIn(b"Recommended Bullet Point Rewrites", post_res.data)

        # Verify database record
        saved_cv = db.query_one("SELECT * FROM CV_ANALYSIS WHERE user_id = :uid", {"uid": uid})
        self.assertIsNotNone(saved_cv)
        analysis_id = saved_cv["cv_analysis_id"]

        # 3. View Analysis Report
        view_res = self.client.get(f"/cv/{analysis_id}")
        self.assertEqual(view_res.status_code, 200)
        self.assertIn(b"Composite Resume Score", view_res.data)

        # 4. Import Skills into Student Profile
        import_res = self.client.post(
            f"/cv/import-skills/{analysis_id}",
            data={
                "skills": ["FastAPI", "Docker", "Redis"],
                "proficiency": "Intermediate",
            },
            follow_redirects=True,
        )
        self.assertEqual(import_res.status_code, 200)

        # Verify skills are saved in STUDENT_SKILLS table
        student_skills = db.query_all(
            "SELECT s.name, ss.proficiency FROM STUDENT_SKILLS ss "
            "JOIN SKILLS s ON ss.skill_id = s.skill_id "
            "WHERE ss.user_id = :uid",
            {"uid": uid},
        )
        names = [s["name"] for s in student_skills]
        self.assertIn("FastAPI", names)
        self.assertIn("Docker", names)

        # 5. Delete CV Analysis
        del_res = self.client.post(f"/cv/{analysis_id}/delete", follow_redirects=True)
        self.assertEqual(del_res.status_code, 200)
        check_del = db.query_one("SELECT * FROM CV_ANALYSIS WHERE cv_analysis_id = :aid", {"aid": analysis_id})
        self.assertIsNone(check_del)

    # ---------------------------------------------------------
    # 4. APPLICATION ASSISTANT BLUEPRINT ROUTES
    # ---------------------------------------------------------

    def test_assistant_generate_and_edit_flow(self):
        """Test drafting cover letter, email, editing, and deleting application documents."""
        uid = self._register_and_login()
        jid = self._create_sample_job(uid)

        # 1. Access Assistant Dashboard
        res = self.client.get("/assistant/")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Application Assistant", res.data)

        # 2. Access Generation Page
        gen_get = self.client.get(f"/assistant/generate?job_id={jid}")
        self.assertEqual(gen_get.status_code, 200)
        self.assertIn(b"Draft Tailored Application Materials", gen_get.data)

        # 3. Generate Cover Letter
        cl_res = self.client.post(
            "/assistant/generate",
            data={
                "job_id": str(jid),
                "doc_type": "cover_letter",
                "tone": "formal",
            },
            follow_redirects=True,
        )
        self.assertEqual(cl_res.status_code, 200)
        self.assertIn(b"Cover Letter", cl_res.data)
        self.assertIn(b"Stripe", cl_res.data)

        # Verify DB record
        cl_doc = db.query_one(
            "SELECT * FROM GENERATED_DOCUMENTS WHERE user_id = :uid AND doc_type = 'cover_letter'",
            {"uid": uid},
        )
        self.assertIsNotNone(cl_doc)
        doc_id = cl_doc["doc_id"]

        # 4. Edit Document In-Place
        edited_text = "Updated customized cover letter content for Stripe."
        edit_res = self.client.post(
            f"/assistant/{doc_id}/edit",
            data={"content": edited_text},
            follow_redirects=True,
        )
        self.assertEqual(edit_res.status_code, 200)
        self.assertIn(b"Document changes successfully saved!", edit_res.data)

        # Verify in DB
        updated_doc = db.query_one("SELECT content FROM GENERATED_DOCUMENTS WHERE doc_id = :did", {"did": doc_id})
        self.assertEqual(updated_doc["content"], edited_text)

        # 5. Generate Application Email
        email_res = self.client.post(
            "/assistant/generate",
            data={
                "job_id": str(jid),
                "doc_type": "email",
                "tone": "friendly",
            },
            follow_redirects=True,
        )
        self.assertEqual(email_res.status_code, 200)
        self.assertIn(b"Outreach Email", email_res.data)

        # 6. Delete Document
        del_res = self.client.post(f"/assistant/{doc_id}/delete", follow_redirects=True)
        self.assertEqual(del_res.status_code, 200)
        check_del = db.query_one("SELECT * FROM GENERATED_DOCUMENTS WHERE doc_id = :did", {"did": doc_id})
        self.assertIsNone(check_del)


if __name__ == "__main__":
    unittest.main()
