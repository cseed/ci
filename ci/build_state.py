from constants import *
from ci_logging import *
import json

def build_state_from_gh_json(d):
    assert isinstance(d, list), d
    assert all(d, lambda x: isinstance(x, dict)), d
    my_statuses = [status for status in d if status['context'] == CONTEXT]
    if len(my_statuses) != 0:
        latest_status = my_statuses[0]
        state = latest_status['state']
        assert state in ['pending', 'failure', 'success'], state # 'error' is allowed by github but not used by me
        description = latest_status['description']
        try:
            matches = re.findall(r'({.*})$', description)
            assert len(matches) == 1, f'{d} {matches}'
            build_state_json = json.loads(matches[0])
        except Exception as e:
            log.exception(
                'could not parse build state from description {latest_status}')
            return None, Unknown()

        t = build_state_json['type']
        if t == 'Deployed':
            build_state = Deployed(t['job_id'], t['merged_sha'])
        elif t == 'Deploying':
            build_state = Deploying(t['job_id'], t['merged_sha'])
        elif t == 'Deployable':
            build_state = Deployable(t['merged_sha'])
        elif t == 'Failure':
            build_state = Failure(t['exit_code'])
        elif t == 'NoMergeSHA':
            build_state = NoMergeSHA(t['exit_code'])
        elif t == 'Building':
            build_state = Building(t['job_id'])
        elif t == 'Buildable':
            build_state = Buildable(t['image'])
        else:
            log.error('found unknown build_state: {build_state_json} {latest_status}')
            return None, Unknown()

        return t['target_sha'], build_state
    else:
        return None, Unknown()

# FIXME: to_json and str for all of these

class Deployed(object):
    def __init__(self, job_id, merged_sha):
        self.job_id = job_id
        self.merged_sha = merged_sha

class Deploying(object):
    def __init__(self, job_id, merged_sha):
        self.job_id = job_id
        self.merged_sha = merged_sha

    def deployed(self):
        return Deployed(self.job_id, self.merged_sha)

class Deployable(object):
    def __init__(self, merged_sha):
        self.merged_sha = merged_sha

    def deploy(self, job_id):
        return Deploying(job_id, self.merged_sha)

class Failure(object):
    def __init__(self, exit_code):
        self.exit_code = exit_code

    def retry(self, job_id):
        return Building(job_id)

class NoMergeSHA(object):
    def __init__(self, exit_code):
        self.exit_code = exit_code

    def retry(self, job_id):
        return Building(job_id)

class Building(object):
    def __init__(self, job_id):
        self.job_id = job_id

    def success(self, merged_sha):
        return Deployable(merged_sha)

    def failure(self, exit_code):
        return Failure(exit_code)

    def no_merge_sha(self, exit_code):
        return NoMergeSHA(exit_code)

class Buildable(object):
    def __init__(self, image):
        self.image = image

    def building(self, job_id):
        return Building(job_id)

class Unknown(object):
    def __init__():
        pass

    def buildable(self, image):
        return Buildable(image)
