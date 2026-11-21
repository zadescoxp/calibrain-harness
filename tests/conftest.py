import os

# Keep the developer's local .env (live strategy settings, keys) out of the tests. Must run before
# ctb.config is imported, since its field defaults read the environment at import time.
os.environ["CTB_ENV_FILE"] = os.devnull
