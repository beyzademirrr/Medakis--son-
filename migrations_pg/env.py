"""Alembic environment: online mode only, owner role only, explicit target database."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import text

from medakis.repositories.credentials import OWNER_ROLE
from medakis.repositories.database import migration_engine
from medakis.repositories.schema import metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = metadata


def run_migrations_online() -> None:
    args = context.get_x_argument(as_dictionary=True)
    database = args.get("db")
    if not database:
        raise SystemExit("Hedef veritabani zorunlu: alembic -x db=medakis_test ...")
    if database == "medakis_demo" and args.get("demo_onayi") != "medakis_demo":
        raise SystemExit("medakis_demo korumali: -x demo_onayi=medakis_demo olmadan migration calismaz.")
    command = getattr(config.cmd_opts, "cmd", None)
    command_name = getattr(command[0], "__name__", "") if command else ""
    if command_name == "downgrade" and database != "medakis_test" and args.get("onay") != database:
        raise SystemExit(f"{database} uzerinde downgrade veri siler: -x onay={database} gerekir (once yedek alin).")

    engine = migration_engine(database)
    with engine.connect() as connection:
        user = connection.execute(text("SELECT current_user")).scalar_one()
        if user != OWNER_ROLE:
            raise SystemExit(f"Migration yalniz {OWNER_ROLE} ile calisir (baglanilan: {user}).")
        connection.commit()
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    raise SystemExit("Offline mod desteklenmiyor.")
run_migrations_online()
