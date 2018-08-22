from batch.client import Job
from ci_logging import log
from git_state import FQRef, FQSHA
from http_helper import put_repo
from pr import PR, GitHubPR
import json


class PRS(object):
    def __init__(self):
        self.target_source_pr = {}
        self.source_target_pr = {}

    def _set(self, source, target, pr):
        assert isinstance(source, FQRef), source
        assert isinstance(target, FQRef), target
        assert isinstance(pr, PR), pr
        if target not in self.target_source_pr:
            self.target_source_pr[target] = {}
        if source not in self.source_target_pr:
            self.source_target_pr[source] = {}
        self.target_source_pr[target][source] = pr
        self.source_target_pr[source][target] = pr

    def _get(self, source=None, target=None, default=None):
        if source is None:
            assert isinstance(target, FQRef), target
            return self.target_source_pr.get(target,
                                             {}
                                             if default is None else default)
        elif target is None:
            assert isinstance(source, FQRef), source
            return self.source_target_pr.get(source,
                                             {}
                                             if default is None else default)
        else:
            assert isinstance(target, FQRef) and isinstance(
                source, FQRef), f'{target} {source}'
            return self.target_source_pr.get(target, {}).get(source, default)

    def _pop(self, source, target):
        assert isinstance(source, FQRef)
        assert isinstance(target, FQRef)
        self.target_source_pr.get(target, {}).pop(source, None)
        return self.source_target_pr.get(source, {}).pop(target, None)

    def __str__(self):
        return json.dumps(self.to_json())

    def to_json(self):
        return [
            y.to_json() for x in self.target_source_pr.values()
            for y in x.values()
        ]

    def exists(self, source, target):
        assert isinstance(source, FQSHA), source
        assert isinstance(target, FQSHA), target
        pr = self._get(source.ref, target.ref)
        return pr and pr.source.sha == source.sha and pr.target.sha == target.sha

    def live_targets(self):
        return self.target_source_pr.keys()

    def live_target_refs(self):
        return [x for x in self.target_source_pr.keys()]

    def for_target(self, target):
        return self.target_source_pr.get(target, {}).values()

    def ready_to_merge(self, target):
        return [pr for pr in self.for_target(target) if pr.is_mergeable()]

    def heal(self):
        for target in self.live_targets():
            self.heal_target(target)

    def heal_target(self, target):
        assert isinstance(target, FQRef)
        ready_to_merge = self.ready_to_merge(target)
        if len(ready_to_merge) != 0:
            pr = ready_to_merge[-1]
            self.deploy(pr)
        else:
            self.build_next(target)

    def build_next(self, target):
        approved = [pr for pr in self.for_target(target) if pr.is_approved()]
        running = [x for x in approved if x.is_running()]
        if len(running) != 0:
            to_build = []
        else:
            approved_and_need_status = [
                x for x in approved if x.is_pending_build()
            ]
            if len(approved_and_need_status) != 0:
                to_build = [approved_and_need_status[-1]]
            else:
                all_pending_prs = [
                    x for x in self.for_target(target) if x.is_pending_build()
                ]
                to_build = all_pending_prs
        log.info(f'next to build for {target.repo.qname}: {[str(x) for x in to_build]}')
        for pr in to_build:
            self._set(pr.source.ref, pr.target.ref, pr.build_it())

    def push(self, new_target):
        assert isinstance(new_target, FQSHA), new_target
        prs = self._get(target=new_target.ref).values()
        if len(prs) == 0:
            log.info(f'no PRs for target {new_target}')
        else:
            for pr in prs:
                self._set(pr.source.ref,
                          pr.target.ref,
                          pr.update_from_github_push(new_target))
            self.heal_target(new_target.ref)

    def pr_push(self, gh_pr):
        assert isinstance(gh_pr, GitHubPR), gh_pr
        pr = self._get(gh_pr.source.ref, gh_pr.target_ref)
        if pr is None:
            log.warning(f'found new PR {gh_pr}')
            pr = gh_pr.to_PR(start_build=True)
        else:
            pr = pr.update_from_github_pr(gh_pr)
        self._set(gh_pr.source.ref, gh_pr.target_ref, pr)

    def forget_target(self, target):
        assert isinstance(target, FQRef), f'{type(target)} {target}'
        sources = self.target_source_pr.pop(target, {}).keys()
        for source in sources:
            x = self.source_target_pr[source]
            del x[target]

    def forget(self, source, target):
        assert isinstance(source, FQRef)
        assert isinstance(target, FQRef)
        self._pop(source, target)

    def review(self, gh_pr, state):
        assert isinstance(gh_pr, GitHubPR), gh_pr
        assert state in ['pending', 'approved', 'changes_requested']
        pr = self._get(gh_pr.source.ref, gh_pr.target_ref)
        if pr is None:
            log.warning(f'found new PR during review update {gh_pr}')
            pr = gh_pr.to_PR()
        self._set(gh_pr.source.ref,
                  gh_pr.target_ref,
                  pr.update_from_github_review_state(state))

    def build_finished(self, source, target, job):
        assert isinstance(job, Job), job
        pr = self._get(source.ref, target.ref)
        if pr is None:
            log.warning(
                f'ignoring job {job.id} {job.attributes} for unknown {source} '
                f'and {target}'
            )
            return
        self._set(source.ref,
                  target.ref,
                  pr.update_from_completed_batch_job(job))
        # eagerly heal because a finished job might mean new work to do
        self.heal_target(target.ref)

    def refresh_from_job(self,
                         source,
                         target,
                         job):
        assert isinstance(job, Job), job
        assert isinstance(source, FQSHA), source
        assert isinstance(target, FQSHA), target
        pr = self._get(source.ref, target.ref)
        if pr is None:
            log.warning(
                f'ignoring job {job.id} for unknown source and target'
            )
            return
        assert source.sha == pr.source.sha, f'{source} {pr}'
        assert target.sha == pr.target.sha, f'{target} {pr}'
        self._set(source.ref, target.ref, pr.refresh_from_batch_job(job))

    def refresh_from_github_build_status(self, gh_pr, status):
        assert isinstance(gh_pr, GitHubPR), gh_pr
        pr = self._get(gh_pr.source.ref, gh_pr.target_ref)
        if pr is None:
            log.warning(
                f'found new PR during GitHub build status update {gh_pr}')
            pr = gh_pr.to_PR()
        self._set(gh_pr.source.ref,
                  gh_pr.target_ref,
                  pr.update_from_github_status(status))

    def build(self, source, target):
        assert isinstance(source, FQRef)
        assert isinstance(target, FQRef)
        pr = self._get(source, target)
        if pr is None:
            raise ValueError(f'no such pr {source} {target}')
        self._set(source, target, pr.build_it())

    def deploy(self, pr):
        assert isinstance(pr, PR)
        log.info(f'merging {pr}')
        (gh_response, status_code) = put_repo(
             pr.target.ref.repo.qname,
             f'pulls/{pr.number}/merge',
             json={
                 'merge_method': 'squash',
                 'sha': pr.source.sha
             },
             status_code=[200, 409])
        if status_code == 200:
            log.info(f'successful merge of {pr}')
            self._set(pr.source.ref, pr.target.ref, pr.merged())
        else:
            assert status_code == 409, f'{status_code} {gh_response}'
            log.warning(
                f'failure to merge {pr} due to {status_code} {gh_response}, '
                f'removing PR, github state refresh will recover and retest '
                f'if necessary')
            self.forget(pr.source.ref, pr.target.ref)
        # FIXME: eagerly update statuses for all PRs targeting this branch
