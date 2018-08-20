from batch.client import *
from build_state import *
from ci_logging import log
from constants import *
from git_state import *
from http_helper import *
from sentinel import *

def try_new_build(source, target):
    img = maybe_get_image(target, source)
    if img:
        attributes = {
            'target': target.to_json(),
            'source': source.to_json(),
            'type': BUILD_JOB_TYPE
        }
        try:
            job = batch_client.create_job(
                img,
                command=[
                    '/bin/bash',
                    '-c',
                    PR_BUILD_SCRIPT
                ],
                env={
                    'SOURCE_REPO_URL': source.ref.repo.url,
                    'SOURCE_BRANCH': source.ref.ref,
                    'SOURCE_SHA': source.sha,
                    'TARGET_REPO_URL': target.ref.repo.url,
                    'TARGET_BRANCH': target.ref.ref,
                    'TARGET_SHA': target.sha
                },
                resources={
                    'requests': {
                        'cpu' : '3.7',
                        'memory': '4G'
                    }
                },
                tolerations=[{
                    'key': 'preemptible',
                    'value': 'true'
                }],
                callback=SELF_HOSTNAME + '/ci_build_done',
                attributes=attributes,
                volumes=[{
                    'volume': { 'name' : f'hail-ci-{VERSION}-service-account-key',
                                'secret' : { 'optional': False,
                                             'secretName': f'hail-ci-{VERSION}-service-account-key' } },
                    'volume_mount': { 'mountPath': '/secrets',
                                      'name': f'hail-ci-{VERSION}-service-account-key',
                                      'readOnly': True }
                }]
            )
            return Building(job)
        except Exception as e:
            log.exception('could not start batch job due to {e}')
            return Buildable(img)
    else:
        return NoImage()

def determine_buildability(source, target):
    img = maybe_get_image(source, target)
    if img:
        return Buildable(img)
    else:
        return NoImage()

def maybe_get_image(source, target):
    assert isinstance(source, FQSHA)
    assert isinstance(target, FQSHA)
    d = os.getcwd()
    try:
        trepo = target.ref.repo
        srepo = source.ref.repo
        if not os.path.isdir(trepo.qname):
            os.makedirs(trepo.qname, exist_ok=True)
            os.chdir(trepo.qname)
            run(['git', 'clone', trepo.url, '.'], check=True)
        else:
            os.chdir(trepo.qname)
        if run(['/bin/sh', '-c', f'git remote | grep -q {srepo.qname}']).returncode != 0:
            run(['git', 'remote', 'add', srepo.qname, srepo.url], check=True)
        run(['git', 'fetch', 'origin'], check=True)
        run(['git', 'fetch', srepo.qname], check=True)
        run(['git', 'checkout', target.sha], check=True)
        run(['git', 'config', '--global', 'user.email', 'hail-ci-leader@example.com'], check=True)
        run(['git', 'config', '--global', 'user.name', 'hail-ci-leader'], check=True)
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
    def __init__(state, number, title, source, target):
        assert state in ['closed', 'open']
        assert isinstance(number, str)
        assert isinstance(title, str)
        assert isinstance(source, FQSHA)
        assert isinstance(target, FQSHA)
        assert review in ['pending', 'approved', 'changes_requested']
        self.state = state
        self.number = number
        self.title = title
        self.source = source
        self.target = target

    @staticmethod
    def from_gh_json(self, d):
        return GitHubPR(
            d['state'],
            str(d['number']),
            str(d['title']),
            FQSHA.from_gh_json(d['head']),
            FQSHA.from_gh_json(d['base'])
        )

    def __str__(self):
        return str(self.to_json())

    def to_json(self):
        return {
            'state': self.state,
            'number': self.number,
            'title': self.title,
            'source': self.source.to_json(),
            'target': self.target.to_json()
        }

class PR(object):
    def __init__(self, target, source, review, build, number, title):
        assert isinstance(target, FQSHA)
        assert isinstance(source, FQSHA)
        assert isinstance(number, str)
        assert review in ['pending', 'approved', 'changes_requested']
        self.target = target
        self.source = source
        self.review = review
        self.build = build
        self.number = number
        self.title = title

    keep = Sentinel()

    def copy(self,
             target=keep,
             source=keep,
             review=keep,
             build=keep,
             number=keep,
             title=keep):
        return PR(
            target=self.target if target is PR.keep else target,
            source=self.source if source is PR.keep else source,
            review=self.review if review is PR.keep else review,
            build=self.build if build is PR.keep else build,
            number=self.number if number is PR.keep else number,
            title=self.title if title is PR.keep else title)

    def _maybe_new_shas(new_source=None, new_target=None):
        if new_source and self.source != new_source:
            if new_target and self.target != new_target:
                log.info(f'found new source and target sha {new_target} {new_source} {self}')
                result = result._new_target_and_source(
                    new_target,
                    new_source
                )
            else:
                assert new_source is not None
                log.info(f'found new source sha {new_source} {self}')
                result = result._new_source(new_source)
        else:
            assert new_target is not None
            log.info(f'found new target sha {new_target} {self}')
            if new_target and self.target != new_target:
                result = result._new_target(new_target)

    def _new_target_and_source(new_target, new_source):
        return self.copy(
            source=new_source,
            target=new_target,
            review='pending'
        )._new_build(
            try_new_build(new_source, new_target)
        )

    def _new_target(new_target):
        img = maybe_build_image(self.source, new_target)
        return self.copy(
            target=new_target
        )._new_build(
            determine_buildability(self.source, new_target)
        )

    def _new_source(new_source):
        img = maybe_build_image(new_source, self.target)
        return self.copy(
            source=new_source
            review='pending'
        )._new_build(
            try_new_build(new_source, self.target)
        )

    def _new_build(new_build):
        if self.build != new_build:
            self.notify_github(new_build)
        return self.copy(
            build=self.build.transition(new_build)
        )

    def build():
        assert (
            isinstance(self.build, Buildable) or
            isinstance(self.build, Failure)
        )
        _new_build(try_new_build(self.source, self.target))

    def notify_github(build):
        json = {
            'state': build.gh_state,
            'description': str(build),
            'context': CONTEXT
        }
        json['target_url'] = url
        try:
            post_repo(
                self.target.ref.repo.url,
                'statuses/' + self.source.sha,
                json=json,
                status_code=201
            )
        except BadStatus as e:
            if e.status_code == 422:
                log.exception(
                    f'Too many statuses applied to {source_sha}! This is a '
                    f'dangerous situation because I can no longer block merging '
                    f'of failing PRs.')
            else:
                raise e

    @staticmethod
    def fresh(source, target, number, title):
        return PR(
            target,
            source,
            'pending',
            Unknown(),
            number,
            title)

    def __str__(self):
        return str(self.to_json())

    def to_json(self):
        return {
            'target': self.target.to_json(),
            'source': self.source.to_json(),
            'review': self.review,
            'build': self.build,
            'number': self.number,
            'title': self.title
        }

    def is_mergeable(self):
        return (
            isinstance(self.build, Deployable) and
            self.review == 'approved'
        )

    def is_approved(self):
        return self.review == 'approved'

    def is_running(self):
        return isinstance(self.build, Building)

    def is_pending_build(self):
        return isinstance(self.build, Buildable)

    def update_from_github_push(push):
        assert isinstance(push, FQSHA)
        assert self.target.ref == push.ref, f'{push} {self}'
        return self._maybe_new_shas(target=push)

    def update_from_github_pr(gh_pr):
        assert isinstance(gh_pr, GitHubPR)
        assert self.target.ref == gh_pr.target.ref
        assert self.source.ref == gh_pr.source.ref
        result = self._maybe_new_shas(
            source=gh_pr.source,
            target=gh_pr.target
        )
        if self.title != gh_pr.title:
            log.info(f'found new title from github {gh_pr.title} {self}')
            result = result.copy(title=gh_pr.title)
        if self.number != gh_pr.number:
            log.info(f'found new PR number from github {gh_pr.title} {self}')
            result = result.copy(number=gh_pr.number)
        return result

    def update_from_github_review_state(review):
        log.info(f'review_state changing from {self.review} to {review}')
        # FIXME: start deploy flow if approved and success
        return self.copy(review=review)

    def update_from_github_status(build):
        if isinstance(self.build, Unknown):
            if self.target.sha == build.target_sha:
                log.info(f'recovering from unknown build state via github. {build} {self}')
                return self.copy(build=build)
            else:
                log.info(
                    'ignoring github build state for wrong target. '
                    f'{target_sha} {build} {self}')
                return self
        else:
            log.info(f'ignoring github build state. {build} {self}')
            return self

    def refresh_from_batch_job(job):
        state = job.cached_status()['state']
        if state == 'Complete':
            return self.update_from_completed_batch_job(job)
        elif state == 'Cancelled':
            log.error(f'a job for me was cancelled {job} {self}')
            return self._new_build(try_new_build(self.target, self.source))
        else:
            assert state == 'Created', f'{state} {job} {self}'
            return self._new_build(Building(job))

    def update_from_completed_batch_job(job):
        assert isinstance(job, Job)
        job_status = job.cached_status()
        exit_code = job_status['exit_code']
        job_target = FQSHA.from_json(job.attributes['target'])
        job_source = FQSHA.from_json(job.attributes['source'])
        assert job_target.ref == self.target.ref
        assert job_source.ref == self.source.ref

        if job_target.sha != self.target.sha:
            log.info(f'notified of job for old target {job.id} {job.attributes} {self}')
            return self
        if job_source_sha != self.source.sha:
            log.info(f'notified of job for old source {job.id} {job.attributes} {self}')
            return self
        if exit_code == 0:
            log.info(f'job finished success {job.id} {job.attributes} {self}')
            return self._new_build(Deployable('NO SHAS YET'))
        else:
            log.info(f'job finished success {job.id} {job.attributes} {self}')
            return self._new_build(Failure(exit_code))

