"""
extensions.py

Shared Flask extension singletons.

These objects are created here WITHOUT being bound to any app instance.
They are initialised inside create_app() via their init_app() methods,
which allows the app factory pattern and makes testing with different
configs (e.g. in-memory SQLite) straightforward.

Usage in models and routes:
    from app.extensions import db, login_manager
"""

from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_bcrypt import Bcrypt
from flask_migrate import Migrate

# SQLAlchemy ORM instance — all DB access goes through this object.
# Never instantiate a second SQLAlchemy() anywhere in the codebase.
db = SQLAlchemy()

# Flask-Login session manager — handles current_user and @login_required.
login_manager = LoginManager()

# Bcrypt for password hashing — never store plain-text passwords.
bcrypt = Bcrypt()

# Alembic migration support — use `flask db migrate` to generate migrations.
migrate = Migrate()
