from pr import *

class PRS(object):
    def __init__(self):
        self.target_source_pr = {}
        self.source_target_pr = {}

    def _set(self, source, target, status):
        assert isinstance(source, FQRef)
        assert isinstance(target, FQRef)
        if x not in self.target_source_pr:
            self.target_source_pr[x] = {}
        if x not in self.source_target_pr:
            self.source_target_pr[x] = {}
        self.target_source_pr[target][source] = status
        self.source_target_pr[source][target] = status

    def _get(self, source=None, target=None, default=None):
        if source is None:
            assert isinstance(target, FQRef), target
            return self.target_source_pr.get(target, {} if default is None else default)
        elif target is None:
            assert isinstance(source, FQRef), source
            return self.source_target_pr.get(source, {} if default is None else default)
        else:
            assert isinstance(target, FQRef) and isinstance(source, FQRef), f'{target} {source}'
            return self.target_source_pr.get(target, {}).get(source, default)

    def _pop(self, source, target):
        assert isinstance(source, FQRef)
        assert isinstance(target, FQRef)
        self.target_source_pr[target].pop(source, None)
        return self.source_target_pr[source].pop(target, None)

    def _update(self, source, target, f):
        pr = self._get(source,
                       target,
                       PR.bottom(gh_pr.source,
                                 gh_pr.target,
                                 gh_pr.number,
                                 gh_pr.title))
        pr = f(pr)
        if pr is not None:
            self._set(source, target, pr)

    def live_targets(self):
        return [x.ref for x in self.target_source_pr.keys()]

    def for_target(self, target):
        return [x for x in self.target_source_pr.get(target {}).values()]

    def push(self, new_target):
        assert isinstance(new_target, FQSHA)
        prs = self._get(target=new_target.ref).values()
        if len(prs) == 0:
            log.info('no PRs for {new_target}')
        else:
            for pr in prs:
                new_status = pr.update_from_github_push(new_target)
                self._set(pr.source.ref, pr.target.ref, new_status)

    def pr_push(self, gh_pr):
        assert isinstance(gh_pr, GitHubPR)
        self._update(gh_pr.source,
                     gh_pr.target,
                     lambda pr: pr.update_from_github_pr(gh_pr))

    def forget_target(self, target):
        assert isinstance(target, FQRef)
        sources = self.target_source_pr.pop(target, {}).keys()
        for source in sources:
            x = self.source_target_pr[source]
            del x[target]

    def forget(self, source, target):
        _pop(source, target)

    def review(self, gh_pr, state):
        assert state in ['pending', 'approved', 'changes_requested']
        self._update(gh_pr.source,
                     gh_pr.target,
                     lambda pr: pr.update_from_github_review_state(state))

    def build_finished(self, source, target, job):
        assert isinstance(job, Job), job
        self._update(source, target, lambda pr: pr.update_from_completed_batch_job(job))

    def refresh_from_job(self, source, target, job):
        assert isinstance(job, Job), job
        self._update(source, target, lambda pr: pr.refresh_from_batch_job(job))

    def refresh_from_github_build_status(self, gh_pr, status):
        self._update(gh_pr.source,
                     gh_pr.target,
                     lambda pr: pr.update_from_github_status(status))
