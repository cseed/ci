from batch.client import Job
from build_state import \
    Failure, Deployable, Unknown, NoImage, Building, Buildable, Deployed, \
    build_state_from_json
from ci_logging import log
from constants import *
from git_state import FQSHA
from http_helper import *
from real_constants import *
from sentinel import Sentinel
from subprocess import run, CalledProcessError
import json


def review_status(reviews):
    latest_state_by_login = {}
    for review in reviews:
        login = review['user']['login']
        state = review['state']
        # reviews is chronological, so later ones are newer statuses
        latest_state_by_login[login] = state
    at_least_one_approved = False
    for login, state in latest_state_by_login.items():
        if (state == 'CHANGES_REQUESTED'):
            return 'changes_requested'
        elif (state == 'APPROVED'):
            at_least_one_approved = True

    if at_least_one_approved:
        return 'approved'
    else:
        return 'pending'


def try_new_build(source, target):
    img = maybe_get_image(target, source)
    if img:
        attributes = {
            'target': json.dumps(target.to_json()),
            'source': json.dumps(source.to_json()),
            'image': img,
            'type': BUILD_JOB_TYPE
        }
        try:
            job = batch_client.create_job(
                img,
                command=['/bin/bash',
                         '-c',
                         PR_BUILD_SCRIPT],
                env={
                    'SOURCE_REPO_URL': source.ref.repo.url,
                    'SOURCE_BRANCH': source.ref.name,
                    'SOURCE_SHA': source.sha,
                    'TARGET_REPO_URL': target.ref.repo.url,
                    'TARGET_BRANCH': target.ref.name,
                    'TARGET_SHA': target.sha
                },
                resources={'requests': {
                    'cpu': '3.7',
                    'memory': '4G'
                }},
                tolerations=[{
                    'key': 'preemptible',
                    'value': 'true'
                }],
                callback=SELF_HOSTNAME + '/ci_build_done',
                attributes=attributes,
                volumes=[{
                    'volume': {
                        'name': f'hail-ci-{VERSION}-service-account-key',
                        'secret': {
                            'optional': False,
                            'secretName':
                            f'hail-ci-{VERSION}-service-account-key'
                        }
                    },
                    'volume_mount': {
                        'mountPath': '/secrets',
                        'name': f'hail-ci-{VERSION}-service-account-key',
                        'readOnly': True
                    }
                }])
            return Building(job, img, target.sha)
        except Exception as e:
            log.exception(f'could not start batch job due to {e}')
            return Buildable(img, target.sha)
    else:
        return NoImage()


def determine_buildability(source, target):
    img = maybe_get_image(source, target)
    if img:
        return Buildable(img, target.sha)
    else:
        return NoImage()


def maybe_get_image(source, target):
    assert isinstance(source, FQSHA)
    assert isinstance(target, FQSHA)
    d = os.getcwd()
    try:
        srepo = source.ref.repo
        trepo = target.ref.repo
        if not os.path.isdir(trepo.qname):
            os.makedirs(trepo.qname, exist_ok=True)
            os.chdir(trepo.qname)
            run(['git', 'clone', trepo.url, '.'], check=True)
        else:
            os.chdir(trepo.qname)
        if run(['/bin/sh',
                '-c',
                f'git remote | grep -q {srepo.qname}']).returncode != 0:
            run(['git', 'remote', 'add', srepo.qname, srepo.url], check=True)
        run(['git', 'fetch', 'origin'], check=True)
        run(['git', 'fetch', srepo.qname], check=True)
        run(['git', 'checkout', target.sha], check=True)
        run(['git',
             'config',
             'user.email',
             'hail-ci-leader@example.com'],
            check=True)
        run(['git', 'config', 'user.name', 'hail-ci-leader'], check=True)
        run(['git', 'merge', source.sha, '-m', 'foo'], check=True)
        # a force push that removes refs could fail us... not sure what we
        # should do in that case. maybe 500'ing is OK?
        with open('hail-ci-build-image', 'r') as f:
            return f.read().strip()
    except (CalledProcessError | FileNotFoundError) as e:
        os.exception(f'could not get hail-ci-build-image due to {e}')
        return None
    finally:
        run(['git', 'reset', '--merge'], check=True)
        os.chdir(d)


class GitHubPR(object):
    def __init__(self, state, number, title, source, target):
        assert state in ['closed', 'open']
        assert isinstance(number, str)
        assert isinstance(title, str)
        assert isinstance(source, FQSHA)
        assert isinstance(target, FQSHA)
        self.state = state
        self.number = number
        self.title = title
        self.source = source
        self.target = target

    @staticmethod
    def from_gh_json(d):
        assert 'state' in d, d
        assert 'number' in d, d
        assert 'title' in d, d
        assert 'head' in d, d
        assert 'base' in d, d
        return GitHubPR(d['state'],
                        str(d['number']),
                        str(d['title']),
                        FQSHA.from_gh_json(d['head']),
                        FQSHA.from_gh_json(d['base']))

    def __str__(self):
        return json.dumps(self.to_json())

    def to_json(self):
        return {
            'state': self.state,
            'number': self.number,
            'title': self.title,
            'source': self.source.to_json(),
            'target': self.target.to_json()
        }

    def to_PR(self, start_build=False):
        pr = PR.fresh(self.source, self.target, self.number, self.title)
        if start_build:
            return pr.build_it()
        else:
            return pr


class PR(object):
    def __init__(self, source, target, review, build, number, title):
        assert isinstance(target, FQSHA), target
        assert isinstance(source, FQSHA), source
        assert number is None or isinstance(number, str)
        assert title is None or isinstance(title, str)
        assert review in ['pending', 'approved', 'changes_requested']
        self.source = source
        self.target = target
        self.review = review
        self.build = build
        self.number = number
        self.title = title

    keep = Sentinel()

    def copy(self,
             source=keep,
             target=keep,
             review=keep,
             build=keep,
             number=keep,
             title=keep):
        return PR(
            source=self.source if source is PR.keep else source,
            target=self.target if target is PR.keep else target,
            review=self.review if review is PR.keep else review,
            build=self.build if build is PR.keep else build,
            number=self.number if number is PR.keep else number,
            title=self.title if title is PR.keep else title)

    def _maybe_new_shas(self, new_source=None, new_target=None):
        if new_source and self.source != new_source:
            assert not self.is_deployed()
            if new_target and self.target != new_target:
                log.info(
                    f'new source and target sha {new_target} {new_source} {self}'
                )
                return self._new_target_and_source(new_target, new_source)
            else:
                assert new_source is not None
                if self.source != new_source:
                    log.info(f'new source sha {new_source} {self}')
                    return self._new_source(new_source)
                else:
                    return self
        else:
            assert new_target is not None
            if self.target != new_target:
                if not self.is_deployed():
                    log.info(f'new target sha {new_target} {self}')
                    return self._new_target(new_target)
                else:
                    log.info(f'ignoring new target sha for deployed PR {self}')
                    return self
            else:
                return self

    def _new_target_and_source(self, new_target, new_source):
        return self.copy(
            source=new_source,
            target=new_target,
            review='pending'
        )._new_build(
            try_new_build(new_source, new_target)
        )

    def _new_target(self, new_target):
        return self.copy(
            target=new_target
        )._new_build(
            determine_buildability(self.source, new_target)
        )

    def _new_source(self, new_source):
        return self.copy(
            source=new_source,
            review='pending'
        )._new_build(
            try_new_build(new_source, self.target)
        )

    def _new_build(self, new_build):
        if self.build != new_build:
            self.notify_github(new_build)
        return self.copy(build=self.build.transition(new_build))

    def build_it(self):
        return self._new_build(try_new_build(self.source, self.target))

    # FIXME: this should be a verb
    def merged(self):
        return self._new_build(Deployed(-1, 'NO SHAS YET!!', self.target.sha))

    def notify_github(self, build):
        log.info(f'notifying github of {build} {self}')
        json = {
            'state': build.gh_state(),
            'description': str(build),
            'context': CONTEXT
        }
        if isinstance(build, Failure) or isinstance(build, Deployable):
            json['target_url'] = \
                f'https://storage.googleapis.com/{GCS_BUCKET}/{self.source.sha}/{self.target.sha}/index.html'
        try:
            post_repo(
                self.target.ref.repo.qname,
                'statuses/' + self.source.sha,
                json=json,
                status_code=201)
        except BadStatus as e:
            if e.status_code == 422:
                log.exception(
                    f'Too many statuses applied to {self.source.sha}! This is a '
                    f'dangerous situation because I can no longer block merging '
                    f'of failing PRs.')
            else:
                raise e

    @staticmethod
    def fresh(source, target, number=None, title=None):
        return PR(source, target, 'pending', Unknown(), number, title)

    def __str__(self):
        return json.dumps(self.to_json())

    @staticmethod
    def from_json(d):
        assert 'target' in d, d
        assert 'source' in d, d
        assert 'review' in d, d
        assert 'build' in d, d
        assert 'number' in d, d
        assert 'title' in d, d
        return PR(
            FQSHA.from_json(d['source']),
            FQSHA.from_json(d['target']),
            d['review'],
            build_state_from_json(d['build']),
            d['number'],
            d['title'],
        )

    def to_json(self):
        return {
            'target': self.target.to_json(),
            'source': self.source.to_json(),
            'review': self.review,
            'build': self.build.to_json(),
            'number': self.number,
            'title': self.title
        }

    # deprecated
    def is_mergeable(self):
        return self.is_deployable()

    def is_deployable(self):
        return (isinstance(self.build,
                           Deployable) and self.review == 'approved')

    def is_approved(self):
        return self.review == 'approved'

    def is_running(self):
        return isinstance(self.build, Building)

    def is_pending_build(self):
        return isinstance(self.build, Buildable)

    def is_deployed(self):
        return isinstance(self.build, Deployed)

    def update_from_github_push(self, push):
        assert isinstance(push, FQSHA)
        assert self.target.ref == push.ref, f'{push} {self}'
        return self._maybe_new_shas(new_target=push)

    def update_from_github_pr(self, gh_pr):
        assert isinstance(gh_pr, GitHubPR)
        assert self.target.ref == gh_pr.target.ref
        assert self.source.ref == gh_pr.source.ref
        # this will build new PRs when the server restarts
        result = self._maybe_new_shas(
            new_source=gh_pr.source,
            new_target=gh_pr.target)
        if self.title != gh_pr.title:
            log.info(f'found new title from github {gh_pr.title} {self}')
            result = result.copy(title=gh_pr.title)
        if self.number != gh_pr.number:
            log.info(f'found new PR number from github {gh_pr.title} {self}')
            result = result.copy(number=gh_pr.number)
        return result

    def update_from_github_review_state(self, review):
        if self.review != review:
            log.info(f'review state changing from {self.review} to {review} {self}')
            # FIXME: start deploy flow if approved and success
            return self.copy(review=review)
        else:
            return self

    def update_from_github_status(self, build):
        if isinstance(self.build, Unknown):
            if self.target.sha == build.target_sha:
                log.info(
                    f'recovering from unknown build state via github. {build} {self}'
                )
                return self.copy(build=build)
            else:
                log.info('ignoring github build state for wrong target. '
                         f'{build} {self}')
                return self
        else:
            log.info(f'ignoring github build state. {build} {self}')
            return self

    def refresh_from_batch_job(self, job):
        state = job.cached_status()['state']
        log.info(
            f'refreshing from batch job {job.id} {state} {job.attributes} {self}'
        )
        if state == 'Complete':
            return self.update_from_completed_batch_job(job)
        elif state == 'Cancelled':
            log.error(
                f'a job for me was cancelled {job.id} {job.attributes} {self}')
            return self._new_build(try_new_build(self.source, self.target))
        else:
            assert state == 'Created', f'{state} {job.id} {job.attributes} {self}'
            assert 'target' in job.attributes, job.attributes
            assert 'image' in job.attributes, job.attributes
            target = FQSHA.from_json(json.loads(job.attributes['target']))
            image = job.attributes['image']
            return self._new_build(Building(job, image, target.sha))

    def update_from_completed_batch_job(self, job):
        assert isinstance(job, Job)
        job_status = job.cached_status()
        exit_code = job_status['exit_code']
        job_source = FQSHA.from_json(json.loads(job.attributes['source']))
        job_target = FQSHA.from_json(json.loads(job.attributes['target']))
        assert job_source.ref == self.source.ref
        assert job_target.ref == self.target.ref

        if job_target.sha != self.target.sha:
            log.info(
                f'notified of job for old target {job.id}'
                # too noisy: f' {job.attributes} {self}'
            )
            return self
        if job_source.sha != self.source.sha:
            log.info(
                f'notified of job for old source {job.id}'
                # too noisy: f' {job.attributes} {self}'
            )
            return self
        if exit_code == 0:
            log.info(f'job finished success {job.id} {job.attributes} {self}')
            return self._new_build(Deployable('NO SHAS YET', self.target.sha))
        else:
            log.info(f'job finished failure {job.id} {job.attributes} {self}')
            return self._new_build(
                Failure(exit_code,
                        job.attributes['image'],
                        self.target.sha))
