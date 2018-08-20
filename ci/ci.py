from batch_helper import *
from build_state import *
from constants import *
from flask import Flask, request, jsonify
from git_state import *
from google_strage import *
from pr import *
import collections
import requests
import threading
import time

prs = PRS()
watched_repos = set([Repo(x[0], x[1]) for x in (x.split('/') for x in INITIAL_WATCHED_REPOS)])

@app.errorhandler(BadStatus)
def handle_invalid_usage(error):
    log.exception('bad status found when making request')
    return jsonify(error.data), error.status_code

@app.route('/push', methods=['POST'])
def github_push():
    d = request.json
    ref = d['ref']
    if ref.startswith('refs/heads'):
        target_ref = FQRef(Repo.from_gh_json(d['repository']), ref[11:])
        target = FQSHA(target_ref, d['after'])
        prs.push(target)
    else:
        log.info(f'ignoring ref push {ref} because it does not start with "refs/heads/"')
    return '', 200

@app.route('/pull_request', methods=['POST'])
def github_pull_request():
    d = request.json
    action = d['action']
    gh_pr = GitHubPR.from_gh_json(d)
    if action == 'opened' or action == 'synchronize':
        prs.pr_push(gh_pr)
    elif action == 'closed':
        prs.forget(gh_pr.source, gh_pr.target)
    else:
        log.info(f'ignoring pull_request with action {action}')
    return '', 200

@app.route('/pull_request_review', methods=['POST'])
def github_pull_request_review():
    d = request.json
    action = d['action']
    if action == 'submitted':
        gh_pr = GitHubPR.from_gh_json(d['pull_request'])
        state = d['review']['state'].lower()
        if state == 'changes_requested':
            prs.review(gh_pr, state)
        else:
            # FIXME: if we track all reviewers, then we don't need to talk to github
            prs.review(gh_pr, review_status(get_reviews()))
    elif action == 'dismissed':
        # FIXME: if we track all reviewers, then we don't need to talk to github
        prs.review(gh_pr, review_status(get_reviews()))
    else:
        log.info(f'ignoring pull_request_review with action {action}')
    return '', 200

@app.route('/ci_build_done', methods=['POST'])
def ci_build_done():
    d = request.json
    attributes = d['attributes']
    source = FQSHA.from_gh_json(attributes['source'])
    target = FQSHA.from_gh_json(attributes['target'])
    job = Job(batch_client, d['id'], attributes=attributes, _status=d)
    receive_job(source, target, job)
    return '', 200

@app.route('/refresh_batch_state', methods=['POST'])
def refresh_batch_state():
    jobs = batch_client.list_jobs()
    latest_jobs = {}
    for job in jobs:
        attributes = job.attributes
        t = attributes.get('type', None)
        if t and t == BUILD_JOB_TYPE:
            target = FQSHA.from_gh_json(attributes['target'])
            if target.ref.repo in watched_repos:
                source = FQSHA.from_gh_json(attributes['source'])
                key = (source, target)
                job2 = latest_jobs.get(key, None)
                if job2 is None:
                    latest_jobs[key] = job
                else:
                    if (job_ordering(job, job2) > 0)
                        log.info(f'cancelling {job2.id}, preferring {job.id}, {job2.attributes} {job.attributes} ')
                        try_to_cancel_job(job2)
                        latest_jobs[key] = job
                    else:
                        log.info(f'cancelling {job.id}, preferring {job2.id}, {job2.attributes} {job.attributes} ')
                        try_to_cancel_job(job)
    for ((source, target), job) in latest_jobs.items():
        prs.refresh_from_job(source, target, job)
    return '', 200

@app.route('/refresh_github_state', methods=['POST'])
def refresh_github_state():
    for target_repo in watched_repos:
        try:
            pulls = open_pulls(target_repo)
            pulls_by_target = collections.defaultdict(list)
            for pull in pulls:
                gh_pr = GitHubPR.from_gh_json(pull)
                pulls_by_target[gh_pr.target] = gh_pr
            refresh_pulls(pulls_by_target)
            refresh_reviews(pulls_by_target)
            refresh_statuses(pulls_by_target)
        except Exception as e:
            log.exception('could not refresh state for {repo} due to {e}')
    return '', 200

def refresh_pulls(pulls_by_target):
    open_gh_target_refs = set([x.ref for x in pulls.keys()])
    for dead_target_ref in set(prs.live_target_refs()) - open_gh_target_refs:
        forget_target(dead_target_ref)
    for (target, pulls) in pulls_by_target.items():
        for gh_pr in pulls:
            prs.pr_push(gh_pr)
        dead_prs = (
            set([(x.source.ref, x.target.ref) for x in prs.for_target(target)]) -
            set([(x.source.ref, x.target.ref) for x in pulls.source])
        )
        for (source, target) in dead_prs:
            prs.forget(source, target)
    return pulls_by_target

def refresh_reviews(pulls_by_target):
    for (_, pulls) in pulls_by_target.items():
        for gh_pr in pulls:
            reviews = get_repo(
                gh_pr.target.ref.repo,
                'pulls/' + gh_pr.number + '/reviews',
                status_code=200
            )
            state = overall_review_state(reviews)['state']
            prs.review(gh_pr, state)

def refresh_statuses(pulls_by_target):
    for (_, pulls) in pulls_by_target.values():
        for gh_pr in pulls:
            statuses = get_repo(
                gh_pr.target.ref.repo,
                'commits/' + gh_pr.source.sha + '/statuses',
                status_code=200
            )
            prs.refresh_from_github_build_status(
                gh_pr,
                build_state_from_gh_json(statuses)
            )

@app.route('/heal', methods=['POST'])
def heal():
    for target in prs.live_targets():
        ready_to_merge = prs.ready_to_merge(target)
        if len(ready_to_merge) != 0:
            # FIXME: pick oldest one instead
            pr = ready_to_merge[0]
            log.info(f'merging {pr}')
            (gh_response, status_code) = put_repo(
                pr.target.ref.repo.qname,
                f'pulls/{pr.number}/merge',
                json={
                    'merge_method': 'squash',
                    'sha': pr.source.sha
                },
                status_code=[200, 409]
            )
            if status_code == 200:
                log.info(f'successful merge of {pr}')
            else:
                assert status_code == 409, f'{status_code} {gh_response}'
                log.warning(
                    f'failure to merge {pr} due to {status_code} {gh_response}, '
                    f'removing PR, github state refresh will recover and retest '
                    f'if necessary')
                prs.forget(pr.source.ref, pr.target.ref)
            # FIXME: eagerly update statuses for all PRs targeting this branch
        else:
            prs = prs.to_build_next(target)
            log.info('nothing to merge into {target}, will build {prs}')
            for pr in prs:
                pr.build()

###############################################################################

def receive_job(source, target, job):
    upload_public_gs_file_from_string(
        GCS_BUCKET,
        f'{source.sha}/{target.sha}/job-log',
        job_log
    )
    upload_public_gs_file_from_filename(
        GCS_BUCKET,
        f'{source.sha}/{target.sha}/index.html',
        'index.html'
    )
    prs.build_finished(source, target, job)

def get_reviews():
    return get_repo(
        repo,
        'pulls/' + pr_number + '/reviews',
        status_code=200
    )

def polling_event_loop():
    time.sleep(1)
    while True:
        try:
           r = requests.post('http://127.0.0.1:5000/refresh_github_state', timeout=120)
           r.raise_for_status()
           r = requests.post('http://127.0.0.1:5000/refresh_batch_state', timeout=120)
           r.raise_for_status()
           r = requests.post('http://127.0.0.1:5000/heal', timeout=120)
           r.raise_for_status()
           r = requests.post('http://127.0.0.1:5000/gc', timeout=120)
           r.raise_for_status()
        except Exception as e:
            log.error(f'Could not poll due to exception: {e}')
            pass
        time.sleep(REFRESH_INTERVAL_IN_SECONDS)

if __name__ == '__main__':
    threading.Thread(target=polling_event_loop).start()
    app = Flask(__name__)
    app.run(host='0.0.0.0')

