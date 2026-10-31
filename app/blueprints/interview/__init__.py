from flask import Blueprint

interview_bp = Blueprint("interview", __name__, url_prefix="/interview")

from . import routes
