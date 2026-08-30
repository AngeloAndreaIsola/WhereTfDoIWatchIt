
from config.settings import *
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": "/tmp/test_wtf.sqlite3",
    }
}
