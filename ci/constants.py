from real_constants import *
from batch.client import *
from ci_logging import *
import batch
import json
import os

SELF_HOSTNAME = os.environ.get('SELF_HOSTNAME',
                               'http://set_the_SELF_HOSTNAME/')
BATCH_SERVER_URL = os.environ.get('BATCH_SERVER_URL',
                                  'http://set_the_BATCH_SERVER_URL/')
REFRESH_INTERVAL_IN_SECONDS = int(
    os.environ.get('REFRESH_INTERVAL_IN_SECONDS',
                   5 * 60))

try:
    INITIAL_WATCHED_REPOS = json.loads(os.environ.get('WATCHED_REPOS', '[]'))
except Exception as e:
    raise ValueError(
        'environment variable WATCHED_REPOS should be a json array of repos as '
        f'strings e.g. ["hail-is/hail"], but was: `{os.environ.get("WATCHED_REPOS", None)}`',
    ) from e
try:
    with open('pr-build-script', 'r') as f:
        PR_BUILD_SCRIPT = f.read()
except FileNotFoundError as e:
    raise ValueError(
        "working directory must contain a file called `pr-build-script' "
        "containing a string that is passed to `/bin/sh -c'") from e
try:
    with open('oauth-token/oauth-token', 'r') as f:
        oauth_token = f.read()
except FileNotFoundError as e:
    raise ValueError(
        "working directory must contain `oauth-token/oauth-token' "
        "containing a valid GitHub oauth token") from e

log.info(f'BATCH_SERVER_URL {BATCH_SERVER_URL}')
log.info(f'SELF_HOSTNAME {SELF_HOSTNAME}')
log.info(f'REFRESH_INTERVAL_IN_SECONDS {REFRESH_INTERVAL_IN_SECONDS}')
log.info(f'INITIAL_WATCHED_REPOS {INITIAL_WATCHED_REPOS}')

batch_client = BatchClient(url=BATCH_SERVER_URL)
