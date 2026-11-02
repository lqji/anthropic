from clinic.app import create_app
from clinic.config import ConfigError, Settings, load_settings


def _settings_or_exit() -> Settings:
    try:
        return load_settings()
    except ConfigError as exc:
        # SystemExit with a string prints it to stderr and exits 1, without a traceback.
        raise SystemExit(str(exc)) from None


app = create_app(_settings_or_exit())
