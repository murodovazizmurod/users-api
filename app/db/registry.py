"""Imports every model so ``Base.metadata`` is complete.

Alembic autogeneration and the test fixtures import this module rather than
each model package, so adding a module only requires one line here.
"""

from app.db.base import Base
from app.modules.auth.models import RefreshToken, VerificationCode
from app.modules.users.models import User

__all__ = ["Base", "User", "VerificationCode", "RefreshToken"]

metadata = Base.metadata
