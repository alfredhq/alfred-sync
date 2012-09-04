from sqlalchemy import create_engine

from alfred_db.session import Session
from alfred_db.models import User, Organization, Repository

from .github import Github


class SyncHandler(object):

    db_session = None
    github = None

    def __init__(self, database_uri):
        self.database_uri = database_uri

    def run(self, user_id):
        engine = create_engine(self.database_uri)
        self.db_session = Session(bind=engine)
        try:
            self.sync(user_id)
        finally:
            self.db_session.close()

    def sync(self, user_id):
        self.user = self.db_session.query(User).get(user_id)
        self.github = Github(self.user.github_access_token)
        return self.sync_user_repos()

    def sync_user_repos(self):
        stored_repos = self.db_session.query(Repository.id).filter(
            Repository.owner_id==self.user.github_id,
            Repository.owner_type=='user',
        )
        stored_repos = [repo.id for repo in stored_repos]
        saved_repos = []
        for repo in self.github.user_repos(type='owner'):
            saved_repos.append(self.save_repo(repo))
        self.remove_unused_repos(stored_repos, saved_repos)
        return stored_repos, saved_repos

    def save_repo(self, gh_repo):
        gh_owner = self.github.user(gh_repo['owner']['login'])
        repo = self.db_session.query(Repository.id).filter_by(
            github_id=gh_repo['id'],
        ).first()
        if repo is None:
            repo = Repository(
                github_id=gh_repo['id'],
                name=gh_repo['name'],
                url=gh_repo['html_url'],
                owner_name=gh_owner['login'],
                owner_type=gh_owner['type'].lower(),
                owner_id=gh_owner['id']
            )
            self.db_session.add(repo)
        else:
            repo.name = gh_repo['name']
            repo.url = gh_repo['html_url']
            repo.owner_name = gh_owner['login']
            repo.owner_type = gh_owner['type'].lower()
            repo.owner_id = gh_owner['id']
        self.db_session.commit()
        return repo.id

    def remove_unused_repos(self, stored_repos, saved_repos):
        difference = set(stored_repos) - set(saved_repos)
        db.session.query(Repository).delete(Repository.id.in_(difference))
