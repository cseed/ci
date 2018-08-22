from batch.client import Job
from batch_helper import try_to_cancel_job, job_ordering
from build_state import build_state_from_gh_json
from ci_logging import log
from constants import \
    batch_client, \
    INITIAL_WATCHED_REPOS, \
    REFRESH_INTERVAL_IN_SECONDS
from flask import Flask, request, jsonify
from git_state import Repo, FQRef, FQSHA
from github import open_pulls, overall_review_state
from google_storage import \
    upload_public_gs_file_from_filename, \
    upload_public_gs_file_from_string
from http_helper import BadStatus
from http_helper import get_repo
from pr import review_status, GitHubPR
from prs import PRS
from real_constants import BUILD_JOB_TYPE, GCS_BUCKET
import collections
import json
import requests
import threading
import time

prs = PRS()
watched_repos = {
    Repo(x[0], x[1])
    for x in (x.split('/') for x in INITIAL_WATCHED_REPOS)
}

app = Flask(__name__)


@app.errorhandler(BadStatus)
def handle_invalid_usage(error):
    log.exception('bad status found when making request')
    return jsonify(error.data), error.status_code


@app.route('/status')
def status():
    return jsonify({
        'watched_repos': [x.to_json() for x in watched_repos],
        'prs': prs.to_json()
    })


@app.route('/push', methods=['POST'])
def github_push():
    d = request.json
    ref = d['ref']
    if ref.startswith('refs/heads'):
        target_ref = FQRef(Repo.from_gh_json(d['repository']), ref[11:])
        target = FQSHA(target_ref, d['after'])
        prs.push(target)
    else:
        log.info(
            f'ignoring ref push {ref} because it does not start with '
            '"refs/heads/"'
        )
    return '', 200


@app.route('/pull_request', methods=['POST'])
def github_pull_request():
    d = request.json
    assert 'action' in d, d
    assert 'pull_request' in d, d
    action = d['action']
    if action in ('opened', 'synchronize'):
        target_sha = FQSHA.from_gh_json(d['pull_request']['base']).sha
        gh_pr = GitHubPR.from_gh_json(d['pull_request'], target_sha)
        prs.pr_push(gh_pr)
    elif action == 'closed':
        gh_pr = GitHubPR.from_gh_json(d['pull_request'])
        log.info(f'forgetting closed pr {gh_pr}')
        prs.forget(gh_pr.source.ref, gh_pr.target_ref)
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
            # FIXME: track all reviewers, then we don't need to talk to github
            prs.review(
                gh_pr,
                review_status(
                    get_reviews(gh_pr.target_ref.repo,
                                gh_pr.number)))
    elif action == 'dismissed':
        # FIXME: track all reviewers, then we don't need to talk to github
        prs.review(
            gh_pr,
            review_status(get_reviews(gh_pr.target_ref.repo,
                                      gh_pr.number)))
    else:
        log.info(f'ignoring pull_request_review with action {action}')
    return '', 200


@app.route('/ci_build_done', methods=['POST'])
def ci_build_done():
    d = request.json
    attributes = d['attributes']
    source = FQSHA.from_json(json.loads(attributes['source']))
    target = FQSHA.from_json(json.loads(attributes['target']))
    job = Job(batch_client, d['id'], attributes=attributes, _status=d)
    receive_job(source, target, job)
    return '', 200


@app.route('/refresh_batch_state', methods=['POST'])
def refresh_batch_state():
    jobs = batch_client.list_jobs()
    jobs = [
        job for job in jobs
        if job.attributes.get('type', None) == BUILD_JOB_TYPE
    ]
    jobs = [
        (FQSHA.from_json(json.loads(job.attributes['source'])),
         FQSHA.from_json(json.loads(job.attributes['target'])),
         job)
        for job in jobs
    ]
    jobs = [(s, t, j) for (s, t, j) in jobs if prs.exists(s, t)]
    latest_jobs = {}
    for (source, target, job) in jobs:
        key = (source, target)
        job2 = latest_jobs.get(key, None)
        if job2 is None:
            latest_jobs[key] = job
        else:
            if job_ordering(job, job2) > 0:
                log.info(
                    f'cancelling {job2.id}, preferring {job.id}'
                )
                try_to_cancel_job(job2)
                latest_jobs[key] = job
            else:
                log.info(
                    f'cancelling {job.id}, preferring {job2.id}'
                )
                try_to_cancel_job(job)
    for ((source, target), job) in latest_jobs.items():
        prs.refresh_from_job(source, target, job)
    return '', 200


@app.route('/force_retest', methods=['POST'])
def force_retest():
    d = request.json
    source = FQRef.from_json(d['source'])
    target = FQRef.from_json(d['target'])
    prs.build(source, target)
    return '', 200


@app.route('/refresh_github_state', methods=['POST'])
def refresh_github_state():
    for target_repo in watched_repos:
        try:
            pulls = open_pulls(target_repo)
            pulls_by_target = collections.defaultdict(list)
            for pull in pulls:
                gh_pr = GitHubPR.from_gh_json(pull)
                pulls_by_target[gh_pr.target_ref].append(gh_pr)
            refresh_pulls(pulls_by_target)
            refresh_reviews(pulls_by_target)
            # FIXME: I can't fit build state json in the status description
            # refresh_statuses(pulls_by_target)
        except Exception as e:
            log.exception(
                f'could not refresh state for {target_repo} due to {e}')
    return '', 200


def refresh_pulls(pulls_by_target):
    open_gh_target_refs = {x for x in pulls_by_target.keys()}
    for dead_target_ref in set(prs.live_target_refs()) - open_gh_target_refs:
        prs.forget_target(dead_target_ref)
    for (target_ref, pulls) in pulls_by_target.items():
        for gh_pr in pulls:
            prs.pr_push(gh_pr)
        dead_prs = ({x.source.ref
                     for x in prs.for_target(target_ref)} -
                    {x.source.ref
                     for x in pulls})
        for source_ref in dead_prs:
            prs.forget(source_ref, target_ref)
    return pulls_by_target


def refresh_reviews(pulls_by_target):
    for (_, pulls) in pulls_by_target.items():
        for gh_pr in pulls:
            reviews = get_repo(
                gh_pr.target_ref.repo.qname,
                'pulls/' + gh_pr.number + '/reviews',
                status_code=200)
            state = overall_review_state(reviews)['state']
            prs.review(gh_pr, state)


def refresh_statuses(pulls_by_target):
    for pulls in pulls_by_target.values():
        for gh_pr in pulls:
            statuses = get_repo(
                gh_pr.target_ref.repo.qname,
                'commits/' + gh_pr.source.sha + '/statuses',
                status_code=200)
            prs.refresh_from_github_build_status(
                gh_pr,
                build_state_from_gh_json(statuses))


@app.route('/heal', methods=['POST'])
def heal():
    prs.heal()
    return '', 200


###############################################################################


def receive_job(source, target, job):
    upload_public_gs_file_from_string(GCS_BUCKET,
                                      f'{source.sha}/{target.sha}/job-log',
                                      job.cached_status()['log'])
    upload_public_gs_file_from_filename(
        GCS_BUCKET,
        f'{source.sha}/{target.sha}/index.html',
        'index.html')
    prs.build_finished(source, target, job)


def get_reviews(repo, pr_number):
    return get_repo(
        repo.qname,
        'pulls/' + pr_number + '/reviews',
        status_code=200)


def polling_event_loop():
    time.sleep(1)
    while True:
        try:
            r = requests.post(
                'http://127.0.0.1:5000/refresh_github_state',
                timeout=360)
            r.raise_for_status()
            r = requests.post(
                'http://127.0.0.1:5000/refresh_batch_state',
                timeout=360)
            r.raise_for_status()
            r = requests.post('http://127.0.0.1:5000/heal', timeout=360)
            r.raise_for_status()
        except Exception as e:
            log.error(f'Could not poll due to exception: {e}')
        time.sleep(REFRESH_INTERVAL_IN_SECONDS)


if __name__ == '__main__':
    threading.Thread(target=polling_event_loop).start()
    app.run(host='0.0.0.0', threaded=False)
