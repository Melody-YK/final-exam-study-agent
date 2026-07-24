"""Identity boundary exports."""

from study_agent.identity.principal import (
    LOCAL_PRINCIPAL_SUBJECT,
    AuthAdapter,
    AuthenticationMethod,
    AuthRequired,
    CourseScope,
    LocalPrincipalProvider,
    Principal,
    PrincipalProvider,
)

__all__ = [
    "LOCAL_PRINCIPAL_SUBJECT",
    "AuthAdapter",
    "AuthRequired",
    "AuthenticationMethod",
    "CourseScope",
    "LocalPrincipalProvider",
    "Principal",
    "PrincipalProvider",
]
