from flask import Blueprint

matcher_bp = Blueprint("matcher", __name__, url_prefix="/matcher")

from . import routes  # noqa: E402, F401
