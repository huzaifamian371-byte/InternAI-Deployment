import os
import unittest
from app import create_app
import db


class Phase1TestCase(unittest.TestCase):
    test_db_path = None

    def setUp(self):
        # Set testing configuration
        os.environ["FLASK_ENV"] = "testing"
        self.app = create_app("testing")
        self.test_db_path = self.app.config["SQLITE_DB_PATH"]
        self.client = self.app.test_client()
        self.app_context = self.app.app_context()
        self.app_context.push()

        # Seed master skills for tests
        from seed import seed_database
        seed_database(self.app.config)

    def tearDown(self):
        self.app_context.pop()
        if self.test_db_path and os.path.exists(self.test_db_path):
            try:
                os.remove(self.test_db_path)
            except Exception:
                pass

    def test_database_backend_active(self):
        """Verify database pool / backend is initialized and responding."""
        backend = db.get_backend()
        self.assertIn(backend, ["oracle", "sqlite"])
        
        # Test basic query
        res = db.query_one("SELECT 1 AS test_val")
        self.assertIsNotNone(res)

    def test_landing_page_accessible(self):
        """Test public landing page loads with HTTP 200."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"InternAI", response.data)
        self.assertIn(b"Create Student Profile", response.data)

    def test_protected_route_requires_login(self):
        """Test /profile redirects unauthenticated user to login."""
        response = self.client.get("/profile")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/auth/login", response.headers["Location"])

    def test_user_registration_and_login_flow(self):
        """Test complete registration, profile auto-init, and login session."""
        email = "student_test@university.edu"
        password = "SecurePassword123!"
        full_name = "Jane Doe"

        # 1. Register new user
        response = self.client.post(
            "/auth/register",
            data={
                "full_name": full_name,
                "email": email,
                "password": password,
                "confirm_password": password,
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

        # Verify user exists in database
        user = db.query_one(
            "SELECT user_id, email, full_name, role FROM USERS WHERE email = :email",
            {"email": email},
        )
        self.assertIsNotNone(user)
        self.assertEqual(user["email"], email)
        self.assertEqual(user["full_name"], full_name)

        # Verify student profile was automatically created
        profile = db.query_one(
            "SELECT profile_id, user_id FROM STUDENT_PROFILES WHERE user_id = :uid",
            {"uid": user["user_id"]},
        )
        self.assertIsNotNone(profile)

        # 2. Logout
        logout_res = self.client.get("/auth/logout", follow_redirects=True)
        self.assertEqual(logout_res.status_code, 200)

        # 3. Test wrong password
        bad_login = self.client.post(
            "/auth/login",
            data={"email": email, "password": "WrongPassword!"},
            follow_redirects=True,
        )
        self.assertIn(b"Invalid email or password", bad_login.data)

        # 4. Test successful login
        good_login = self.client.post(
            "/auth/login",
            data={"email": email, "password": password},
            follow_redirects=True,
        )
        self.assertEqual(good_login.status_code, 200)
        self.assertIn(b"Student Dashboard", good_login.data)
        self.assertIn(b"Jane Doe", good_login.data)

    def test_profile_update_and_skill_crud(self):
        """Test profile academic updates, skill adding/deleting, and projects CRUD."""
        email = "skills_tester@university.edu"
        password = "Password123#"
        full_name = "Skills Tester"

        # Register and log in
        self.client.post(
            "/auth/register",
            data={
                "full_name": full_name,
                "email": email,
                "password": password,
                "confirm_password": password,
            },
            follow_redirects=True,
        )

        user = db.query_one("SELECT user_id FROM USERS WHERE email = :email", {"email": email})
        uid = user["user_id"]

        # Update profile details
        update_res = self.client.post(
            "/profile/update",
            data={
                "full_name": "Skills Tester Updated",
                "university": "MIT",
                "degree": "B.S. in Artificial Intelligence",
                "graduation_year": "2026",
                "gpa": "3.92",
                "bio": "Passionate AI and machine learning student looking for summer research internships.",
            },
            follow_redirects=True,
        )
        self.assertEqual(update_res.status_code, 200)

        prof = db.query_one("SELECT university, degree, gpa FROM STUDENT_PROFILES WHERE user_id = :uid", {"uid": uid})
        self.assertEqual(prof["university"], "MIT")
        self.assertEqual(prof["degree"], "B.S. in Artificial Intelligence")
        self.assertAlmostEqual(prof["gpa"], 3.92, places=2)

        # Add a skill
        add_skill_res = self.client.post(
            "/profile/skills/add",
            data={
                "skill_name": "Python",
                "proficiency": "Advanced",
                "category": "programming",
            },
            follow_redirects=True,
        )
        self.assertEqual(add_skill_res.status_code, 200)

        std_skill = db.query_one(
            "SELECT ss.student_skill_id, s.name, ss.proficiency "
            "FROM STUDENT_SKILLS ss JOIN SKILLS s ON ss.skill_id = s.skill_id "
            "WHERE ss.user_id = :uid",
            {"uid": uid},
        )
        self.assertIsNotNone(std_skill)
        self.assertEqual(std_skill["name"], "Python")
        self.assertEqual(std_skill["proficiency"], "Advanced")

        # Delete the skill
        del_skill_res = self.client.post(
            f"/profile/skills/{std_skill['student_skill_id']}/delete",
            follow_redirects=True,
        )
        self.assertEqual(del_skill_res.status_code, 200)

        deleted_check = db.query_one(
            "SELECT student_skill_id FROM STUDENT_SKILLS WHERE student_skill_id = :ssid",
            {"ssid": std_skill["student_skill_id"]},
        )
        self.assertIsNone(deleted_check)

        # Add a project
        add_proj_res = self.client.post(
            "/profile/projects/add",
            data={
                "title": "Autonomous Drone Navigation",
                "description": "Implemented SLAM algorithms using Python and OpenCV.",
                "tech_used": "Python, OpenCV, ROS",
                "project_url": "https://github.com/tester/drone-nav",
            },
            follow_redirects=True,
        )
        self.assertEqual(add_proj_res.status_code, 200)

        proj = db.query_one(
            "SELECT project_id, title FROM PROJECTS WHERE user_id = :uid",
            {"uid": uid},
        )
        self.assertIsNotNone(proj)
        self.assertEqual(proj["title"], "Autonomous Drone Navigation")

        # Delete project
        del_proj_res = self.client.post(
            f"/profile/projects/{proj['project_id']}/delete",
            follow_redirects=True,
        )
        self.assertEqual(del_proj_res.status_code, 200)

        deleted_proj = db.query_one(
            "SELECT project_id FROM PROJECTS WHERE project_id = :pid",
            {"pid": proj["project_id"]},
        )
        self.assertIsNone(deleted_proj)


if __name__ == "__main__":
    unittest.main()
