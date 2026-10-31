import functools
import re
from urllib.parse import urlparse, urljoin
from flask import (
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    g,
    abort,
)
from werkzeug.security import generate_password_hash, check_password_hash
from . import auth_bp
import db


def is_safe_url(target: str) -> bool:
    """Ensure redirection target belongs to the same host."""
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ("http", "https") and ref_url.netloc == test_url.netloc


def get_current_user():
    """Retrieve the currently authenticated user from session or database."""
    if "user_id" not in session:
        if hasattr(g, "current_user"):
            g.current_user = None
        return None
    if not hasattr(g, "current_user") or g.current_user is None or g.current_user.get("user_id") != session["user_id"]:
        user = db.query_one(
            "SELECT user_id, email, full_name, role, is_active FROM USERS WHERE user_id = :uid",
            {"uid": session["user_id"]},
        )
        if user and user["is_active"] == 1:
            g.current_user = user
        else:
            session.clear()
            g.current_user = None
    return g.current_user


def login_required(view):
    """Decorator to protect routes requiring authentication."""
    @functools.wraps(view)
    def wrapped_view(**kwargs):
        user = get_current_user()
        if user is None:
            flash("Please sign in to access this page.", "warning")
            return redirect(url_for("auth.login", next=request.full_path or request.path))
        return view(**kwargs)
    return wrapped_view


def admin_required(view):
    """Decorator to protect routes requiring administrator privileges."""
    @functools.wraps(view)
    def wrapped_view(**kwargs):
        user = get_current_user()
        if user is None:
            flash("Please sign in with administrator credentials.", "warning")
            return redirect(url_for("auth.login", next=request.path))
        if user.get("role") != "admin":
            flash("Access denied: Administrator privileges are required.", "danger")
            abort(403)
        return view(**kwargs)
    return wrapped_view


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    """Register a new student account."""
    if get_current_user():
        return redirect(url_for("index"))

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        errors = []
        if not full_name:
            errors.append("Full name is required.")
        if not email or "@" not in email:
            errors.append("A valid email address is required.")
        if len(password) < 8:
            errors.append("Password must be at least 8 characters long.")
        if password != confirm_password:
            errors.append("Passwords do not match.")

        if not errors:
            existing = db.query_one(
                "SELECT user_id FROM USERS WHERE email = :email",
                {"email": email}
            )
            if existing:
                errors.append("An account with that email already exists. Please log in.")

        if errors:
            for error in errors:
                flash(error, "danger")
            return render_template(
                "auth/register.html",
                full_name=full_name,
                email=email
            )

        # Hash password and create user
        password_hash = generate_password_hash(password)
        try:
            user_id = db.execute_returning_id(
                "INSERT INTO USERS (email, password_hash, full_name, role, is_active) "
                "VALUES (:email, :password_hash, :full_name, :role, :is_active)",
                {
                    "email": email,
                    "password_hash": password_hash,
                    "full_name": full_name,
                    "role": "student",
                    "is_active": 1,
                },
                id_column="user_id"
            )

            # Create default student profile entry
            db.execute(
                "INSERT INTO STUDENT_PROFILES (user_id) VALUES (:user_id)",
                {"user_id": user_id}
            )

            # Authenticate session
            session.clear()
            session.permanent = True
            session["user_id"] = user_id
            session["user_email"] = email
            session["user_name"] = full_name
            session["user_role"] = "student"

            flash(f"Welcome to InternAI, {full_name}! Let's set up your profile.", "success")
            return redirect(url_for("profile.index"))
        except Exception as exc:
            flash(f"An error occurred while creating your account: {str(exc)}", "danger")

    return render_template("auth/register.html")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """Authenticate and log in an existing user."""
    if get_current_user():
        return redirect(url_for("index"))

    next_url = request.args.get("next")

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        next_target = request.form.get("next") or next_url

        if not email or not password:
            flash("Please enter both email and password.", "danger")
            return render_template("auth/login.html", email=email, next=next_target)

        user = db.query_one(
            "SELECT user_id, email, password_hash, full_name, role, is_active "
            "FROM USERS WHERE email = :email",
            {"email": email}
        )

        if not user or not check_password_hash(user["password_hash"], password):
            flash("Invalid email or password. Please try again.", "danger")
            return render_template("auth/login.html", email=email, next=next_target)

        if user.get("is_active") != 1:
            flash("Your account has been deactivated. Please contact an administrator.", "warning")
            return render_template("auth/login.html", email=email, next=next_target)

        # Authenticate session
        session.clear()
        session.permanent = True
        session["user_id"] = user["user_id"]
        session["user_email"] = user["email"]
        session["user_name"] = user["full_name"]
        session["user_role"] = user["role"]

        flash(f"Welcome back, {user['full_name']}!", "success")
        if next_target and is_safe_url(next_target):
            return redirect(next_target)
        return redirect(url_for("index"))

    return render_template("auth/login.html", next=next_url)


@auth_bp.route("/logout")
def logout():
    """Log out the current user and destroy the session."""
    session.clear()
    if hasattr(g, "current_user"):
        g.current_user = None
    flash("You have been signed out successfully.", "info")
    return redirect(url_for("auth.login"))
