from git_state import FQRef
from ci_logging import log
from batch.client import BatchClient
import json
import os

SELF_HOSTNAME = os.environ.get('SELF_HOSTNAME',
                               'http://set_the_SELF_HOSTNAME/')
BATCH_SERVER_URL = os.environ.get('BATCH_SERVER_URL',
                                  'http://set_the_BATCH_SERVER_URL/')
REFRESH_INTERVAL_IN_SECONDS = \
    int(os.environ.get('REFRESH_INTERVAL_IN_SECONDS', 60))

try:
    DEPLOYABLE_REFS = [
        FQRef.from_short_str(x)
        for x in json.loads(os.environ.get('DEPLOYABLE_REFS', '[]'))
    ]
except Exception as e:
    raise ValueError(
        'environment variable DEPLOYABLE_REFS should be a json array of refs as '
        f'strings e.g. ["hail-is/hail:master"], but was: `{os.environ.get("DEPLOYABLE_REFS", None)}`',
    ) from e
try:
    with open('pr-build-script', 'r') as f:
        PR_BUILD_SCRIPT = f.read()
except FileNotFoundError as e:
    raise ValueError(
        "working directory must contain a file called `pr-build-script' "
        "containing a string that is passed to `/bin/sh -c'") from e
try:
    with open('pr-deploy-script', 'r') as f:
        PR_DEPLOY_SCRIPT = f.read()
except FileNotFoundError as e:
    raise ValueError(
        "working directory must contain a file called `pr-deploy-script' "
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
log.info(f'DEPLOYABLE_REFS {[x.short_str() for x in DEPLOYABLE_REFS]}')

batch_client = BatchClient(url=BATCH_SERVER_URL)
