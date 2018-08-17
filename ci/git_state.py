class Repo(object):
    def __init__(self, owner, name):
        assert isinstance(owner, str)
        assert isinstance(name, str)
        self.owner = owner
        self.name = name

    def __eq__(self, other):
        return self.owner == other.owner and self.name == other.name

    def __ne__(self, other):
        return not self == other

    def __hash__(self):
        return hash((self.owner, self.name))

    def __str__(self):
        return str(self.to_json())

    def to_json(self):
        return {
            'owner': self.owner,
            'name': self.name
        }

    @staticmethod
    def from_gh_json(d):
        return Repo(d['owner']['login'], d['name'])

class FQRef(object):
    def __init__(self, repo, ref):
        assert isinstance(repo, Repo)
        assert isinstance(ref, str)
        self.repo = repo
        self.ref = ref

    def __eq__(self, other):
        return self.repo == other.repo and self.ref == other.ref

    def __ne__(self, other):
        return not self == other

    def __hash__(self):
        return hash((self.repo, self.ref))

    def __str__(self):
        return str(self.to_json())

    def to_json(self):
        return {
            'repo': self.repo,
            'ref': self.ref
        }

class FQSHA(object):
    def __init__(self, ref, sha):
        assert isinstance(ref, FQRef)
        assert isinstance(sha, str)
        self.ref = ref
        self.sha = sha

    def __eq__(self, other):
        return self.ref == other.ref and self.sha == other.sha

    def __ne__(self, other):
        return not self == other

    def __hash__(self):
        return hash((self.ref, self.sha))

    @staticmethod
    def from_gh_json(d):
        return FQSHA(FQRef(Repo.from_gh_json(d['repo']),
                           d['ref']),
                     d['sha'])

    def __str__(self):
        return str(self.to_json())

    def to_json(self):
        return {
            'ref': self.ref,
            'sha': self.sha
        }

