from dynaconf import Dynaconf

config = Dynaconf(
    envvar_prefix="MLSS",
    settings_files=[".env"],
)
