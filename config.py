from dynaconf import Dynaconf

config = Dynaconf(
    envvar_prefix="MLSS",
    settings_files=[".env"],
    load_dotenv=True,       # loads .env into os.environ so os.environ.get() works
)
