"""Seed script for InternAI.

Populates master skills catalog and creates the initial default administrator account.
Can be executed directly via `python seed.py` or imported into Flask CLI commands.
"""

import logging
from werkzeug.security import generate_password_hash
from config import Config
import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Master skills dictionary (~65 skills across 6 categories)
MASTER_SKILLS = [
    # Programming
    ("Python", "programming", "Versatile high-level programming language for web, data, and automation"),
    ("JavaScript", "programming", "Standard language of the web for client-side and server-side execution"),
    ("TypeScript", "programming", "Typed superset of JavaScript providing static type definitions"),
    ("Java", "programming", "Enterprise object-oriented language for large-scale backend systems"),
    ("C++", "programming", "High-performance compiled language for systems and low-level development"),
    ("C#", "programming", "Modern language for the Microsoft .NET platform and enterprise backends"),
    ("Go", "programming", "Cloud-native concurrent compiled language developed by Google"),
    ("Rust", "programming", "Memory-safe systems programming language without garbage collection"),
    ("PHP", "programming", "Server-side scripting language primarily used for web backends"),
    ("Ruby", "programming", "Dynamic, object-oriented language commonly used with Rails"),
    ("Swift", "programming", "Apple ecosystem language for iOS, iPadOS, and macOS development"),
    ("Kotlin", "programming", "Modern JVM language, official language for Android app development"),

    # Web Development
    ("HTML5", "web", "Standard semantic markup language for structuring web pages"),
    ("CSS3", "web", "Style sheet language for responsive web design and animations"),
    ("React", "web", "Component-based frontend JavaScript library maintained by Meta"),
    ("Next.js", "web", "Full-stack React framework for server-side rendering and static export"),
    ("Vue.js", "web", "Progressive JavaScript framework for building user interfaces"),
    ("Angular", "web", "Google-developed TypeScript framework for enterprise single-page apps"),
    ("Node.js", "web", "V8-powered asynchronous JavaScript runtime for backend services"),
    ("Express.js", "web", "Minimalist web framework for Node.js RESTful APIs"),
    ("Flask", "web", "Lightweight WSGI Python microframework for web services"),
    ("Django", "web", "Batteries-included high-level Python web framework"),
    ("FastAPI", "web", "High-performance Python ASGI web framework with automatic OpenAPI docs"),
    ("REST APIs", "web", "Architectural style for stateless HTTP-based web services"),
    ("GraphQL", "web", "Query language and server runtime for flexible declarative APIs"),
    ("Tailwind CSS", "web", "Utility-first modern CSS framework for rapid UI styling"),
    ("Bootstrap", "web", "Popular component library for responsive web interfaces"),

    # Data & Databases
    ("SQL", "data", "Standard relational database query and data manipulation language"),
    ("PostgreSQL", "data", "Advanced open-source object-relational database management system"),
    ("Oracle DB", "data", "Enterprise multi-model relational database management system"),
    ("MySQL", "data", "Widely adopted relational database for web applications"),
    ("MongoDB", "data", "Document-oriented NoSQL database for flexible JSON-like schemas"),
    ("Redis", "data", "In-memory key-value data store used for caching and pub/sub"),
    ("Pandas", "data", "Python library for tabular data analysis and manipulation"),
    ("NumPy", "data", "Fundamental package for scientific computing with N-dimensional arrays"),
    ("Scikit-Learn", "data", "Machine learning library for predictive data modeling in Python"),
    ("TensorFlow", "data", "End-to-end open source platform for machine learning and deep learning"),
    ("PyTorch", "data", "Optimized tensor library for deep learning using GPUs and CPUs"),
    ("Power BI", "data", "Interactive business intelligence and data visualization platform"),
    ("Tableau", "data", "Visual analytics platform transforming raw data into actionable dashboards"),

    # Cloud & DevOps
    ("AWS", "cloud", "Amazon Web Services cloud computing infrastructure platform"),
    ("Microsoft Azure", "cloud", "Cloud computing services by Microsoft for application hosting and data"),
    ("Google Cloud Platform", "cloud", "Cloud platform providing computing, BigQuery, and AI infrastructure"),
    ("Docker", "cloud", "Containerization platform to package applications and dependencies"),
    ("Kubernetes", "cloud", "Open-source container orchestration system for automating deployment"),
    ("CI/CD Pipelines", "cloud", "Continuous integration and continuous deployment automation practices"),
    ("Terraform", "cloud", "Infrastructure-as-Code tool to define cloud infrastructure declaratively"),
    ("Linux / Bash", "cloud", "Unix-based operating system navigation, system tools, and shell scripting"),
    ("Git", "cloud", "Distributed version control system for source code tracking"),
    ("Microservices", "cloud", "Architectural pattern structuring applications as collections of loose services"),

    # Soft Skills
    ("Problem Solving", "soft_skills", "Analytical reasoning to break down complex issues into solvable parts"),
    ("Team Collaboration", "soft_skills", "Working constructively and cooperatively in cross-functional teams"),
    ("Verbal Communication", "soft_skills", "Articulating ideas clearly and persuasively in technical and general settings"),
    ("Written Communication", "soft_skills", "Documenting architectures, creating reports, and writing concise emails"),
    ("Time Management", "soft_skills", "Prioritizing competing tasks and consistently delivering to deadlines"),
    ("Critical Thinking", "soft_skills", "Objective evaluation of evidence, trade-offs, and alternative solutions"),
    ("Adaptability", "soft_skills", "Thriving in evolving project requirements and fast-paced environments"),
    ("Agile / Scrum", "soft_skills", "Iterative software development framework with standups and sprints"),
    ("Leadership", "soft_skills", "Motivating team members, taking ownership, and guiding project direction"),

    # Tools
    ("Postman", "tools", "API platform for building, testing, and debugging HTTP requests"),
    ("VS Code", "tools", "Extensible source-code editor with rich debugging and plugin ecosystem"),
    ("Jira", "tools", "Issue and project tracking software for agile development teams"),
    ("Figma", "tools", "Collaborative cloud-based UI/UX vector design and prototyping tool"),
    ("GitHub", "tools", "Cloud platform for Git version control, pull requests, and CI actions"),
    ("Wireshark", "tools", "Network protocol analyzer for deep inspection of network traffic"),
    ("Docker Compose", "tools", "Tool for defining and running multi-container Docker applications"),
]


def seed_database(config_override=None) -> None:
    """Populate database with default admin user and skills catalog."""
    if config_override:
        config_dict = dict(config_override)
    else:
        try:
            from flask import current_app
            config_dict = dict(current_app.config) if current_app else None
        except Exception:
            config_dict = None

    if not config_dict:
        config_dict = {
            "DB_BACKEND": Config.DB_BACKEND,
            "SQLITE_DB_PATH": Config.SQLITE_DB_PATH,
            "ORACLE_USER": Config.ORACLE_USER,
            "ORACLE_PASSWORD": Config.ORACLE_PASSWORD,
            "ORACLE_DSN": Config.ORACLE_DSN,
            "ORACLE_POOL_MIN": Config.ORACLE_POOL_MIN,
            "ORACLE_POOL_MAX": Config.ORACLE_POOL_MAX,
            "ORACLE_POOL_INC": Config.ORACLE_POOL_INC,
        }
    
    db.init_pool(config_dict)
    logger.info("Active database backend: %s", db.get_backend())

    # 1. Seed Administrator User
    admin_email = "admin@internai.local"
    existing_admin = db.query_one(
        "SELECT user_id, email FROM USERS WHERE email = :email",
        {"email": admin_email}
    )

    if not existing_admin:
        pwd_hash = generate_password_hash("Admin@123456")
        admin_id = db.execute_returning_id(
            "INSERT INTO USERS (email, password_hash, full_name, role, is_active) "
            "VALUES (:email, :password_hash, :full_name, :role, :is_active)",
            {
                "email": admin_email,
                "password_hash": pwd_hash,
                "full_name": "System Administrator",
                "role": "admin",
                "is_active": 1,
            },
            id_column="user_id"
        )
        # Create corresponding profile
        db.execute(
            "INSERT INTO STUDENT_PROFILES (user_id, bio) VALUES (:user_id, :bio)",
            {"user_id": admin_id, "bio": "InternAI System Administrator"}
        )
        logger.info("Created default admin user: %s (Password: Admin@123456)", admin_email)
    else:
        logger.info("Admin user (%s) already exists.", admin_email)

    # 2. Seed Master Skills
    inserted_skills = 0
    for name, category, description in MASTER_SKILLS:
        exists = db.query_one(
            "SELECT skill_id FROM SKILLS WHERE name = :name",
            {"name": name}
        )
        if not exists:
            db.execute(
                "INSERT INTO SKILLS (name, category, description) "
                "VALUES (:name, :category, :description)",
                {"name": name, "category": category, "description": description}
            )
            inserted_skills += 1

    logger.info("Master skills seeded: %d added (%d total defined).", inserted_skills, len(MASTER_SKILLS))


if __name__ == "__main__":
    seed_database()
