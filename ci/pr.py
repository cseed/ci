from build_state import *
from git_state import *
from ci_logging import log

class Review(object):
    def __init__(self, state):
        assert state in ['approved', 'pending', 'changes_requested']
        self.state = state

def review_status(reviews):
    latest_state_by_login = {}
    for review in reviews:
        login = review['user']['login']
        state = review['state']
        # reviews is chronological, so later ones are newer statuses
        latest_state_by_login[login] = state
    total_state = 'pending'
    for login, state in latest_state_by_login.items():
        if (state == 'CHANGES_REQUESTED'):
            total_state = 'changes_requested'
            break
        elif (state == 'APPROVED'):
            total_state = 'approved'

    return {
        'state': total_state,
        'reviews': latest_state_by_login
    }

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

class PR(object):
    def __init__(self, target, source, review, build, number, title, source_has_been_built_at_least_once):
        assert isinstance(target, FQSHA)
        assert isinstance(source, FQSHA)
        assert isinstance(number, str)
        self.target = target
        self.source = source
        self.review = review
        self.build = build
        self.number = number
        self.title = title
        self.source_has_been_built_at_least_once = source_has_been_built_at_least_once

    def __str__(self):
        return str(self.to_json())

    def to_json(self):
        return {
            'target': self.target,
            'source': self.source,
            'review': self.review,
            'build': self.build,
            'number': self.number,
            'title': self.title
        }

    def update_from_github_pr(gh_pr):
        assert isinstance(gh_pr, GitHubPR)
        assert self.number == gh_pr.number
        assert self.target.ref == gh_pr.target.ref
        assert self.source.ref == gh_pr.source.ref

        if self.source != gh_pr.source:
            new_source = gh_pr.source
            source_has_been_built_at_least_once = False
        else:
            new_source = self.source
            source_has_been_built_at_least_once = True

        if self.target != gh_pr.target:
            new_target = gh_pr.target
        else:
            new_target = self.target

    def update_from_github_review_state(review):
        ???

    def update_from_github_status(target_sha, build):
        if isinstance(self.build, Unknown):
            if self.target.sha == target_sha:
                return self.copy(build=build)
            else:
                log.info(
                    'trying to update status from different target_sha, '
                    f'ignoring: {target_sha}, {self}')
                return self
        else:
            return self

    def update_from_github(gh_pr, review, gh_build):
        return (
            self
            .update_from_github(gh_pr)
            .update_from_github_review_state(review)
            .update_from_github_status(gh_build[0], gh_build[1])
        )

