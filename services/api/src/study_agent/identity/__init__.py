"""Identity boundary exports."""

from study_agent.identity.principal import (
    AuthAdapter,
    AuthenticationMethod,
    AuthRequired,
    CourseScope,
    LocalPrincipalProvider,
    Principal,
    PrincipalProvider,
)

__all__ = [
    "AuthAdapter",
    "AuthRequired",
    "AuthenticationMethod",
    "CourseScope",
    "LocalPrincipalProvider",
    "Principal",
    "PrincipalProvider",
]
