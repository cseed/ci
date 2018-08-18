GITHUB_URL = 'https://api.github.com/'
VERSION = '0-1'
CONTEXT = 'hail-ci-0-1'
BUILD_JOB_PREFIX = 'hail-ci-0-1'
BUILD_JOB_TYPE = BUILD_JOB_PREFIX + '-build'
SELF_HOSTNAME = os.environ['SELF_HOSTNAME']
BATCH_SERVER_URL = os.environ['BATCH_SERVER_URL']

log.info(f'BATCH_SERVER_URL {BATCH_SERVER_URL}')
log.info(f'SELF_HOSTNAME {SELF_HOSTNAME}')

try:
    INITIAL_WATCHED_REPOS = json.loads(os.environ['WATCHED_REPOS'])
except Exception as e:
    raise ValueError(
        'environment variable WATCHED_REPOS should be a json array of repos as '
        f'strings e.g. ["hail-is/hail"], but was: `{os.environ.get("WATCHED_REPOS", None)}`',
    ) from e
try:
    with open('pr-build-script', 'r') as f:
        PR_BUILD_SCRIPT = f.read()
except FileNotFoundError as e:
    raise NoPRBuildScript(
        "working directory must contain a file called `pr-build-script' "
        "containing a string that is passed to `/bin/sh -c'"
    ) from e

batch_client = BatchClient(url=BATCH_SERVER_URL)
