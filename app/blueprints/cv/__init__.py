from flask import Blueprint

cv_bp = Blueprint("cv", __name__, url_prefix="/cv")

from . import routes
