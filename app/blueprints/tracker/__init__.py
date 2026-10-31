from flask import Blueprint

tracker_bp = Blueprint("tracker", __name__, url_prefix="/tracker")

from . import routes
