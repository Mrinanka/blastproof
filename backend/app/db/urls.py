"""Which database URL, and which role, migrations connect with."""

from app.config import Settings

# Owns Blastproof's schema (backend plan §5); the same role name locally and on Neon.
MIGRATOR_ROLE = "blastproof_owner"


class MissingMigratorUrlError(RuntimeError):
    pass


class WrongMigratorRoleError(RuntimeError):
    pass


def migrator_url(explicit: str | None, settings: Settings) -> str:
    """The URL Alembic uses: an explicit one (tests pass one), else BLASTPROOF_MIGRATOR_URL.

    The error never includes a URL, so nothing secret reaches the terminal.
    """
    if explicit:
        return explicit
    configured = settings.blastproof_migrator_url
    # An empty value (a placeholder not filled in yet) counts as missing.
    if configured is not None and configured.get_secret_value():
        return configured.get_secret_value()
    raise MissingMigratorUrlError(
        "BLASTPROOF_MIGRATOR_URL is not set. Migrations run as blastproof_owner; see"
        " backend/.env.example for the local value."
    )


def check_migrator_role(current_user: str) -> None:
    """Refuse to migrate as any role but the owner (BE-14).

    As a superuser, new tables would belong to the superuser, so the owner could never migrate
    them again; as the app role, the run would fail halfway through.
    """
    if current_user != MIGRATOR_ROLE:
        raise WrongMigratorRoleError(
            f"Migrations must run as {MIGRATOR_ROLE}, not {current_user}."
            " Point BLASTPROOF_MIGRATOR_URL at the owner role. Nothing was changed."
        )
