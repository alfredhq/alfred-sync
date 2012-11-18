from alfred_db.session import Session
from alfred_db.models import User, Organization, Repository, Permission
from alfred_db.models.organization import Membership

from github import Github
from sqlalchemy import create_engine

from .utils import generate_token


class SyncHandler(object):

    @classmethod
    def run(cls, database_uri, user_id):
        cls(database_uri)(user_id)

    def __init__(self, database_uri):
        self.engine = create_engine(database_uri)
        self.db_session = Session(bind=self.engine)
        self.user = None
        self.github = None

    def __call__(self, user_id):
        try:
            self.sync(user_id)
        except Exception as e:
            self.db_session.rollback()
            raise e
        else:
            self.db_session.commit()
        finally:
            self.db_session.close()

    def sync(self, user_id):
        self.user = self.db_session.query(User).get(user_id)
        self.github = Github(self.user.github_access_token)
        self.sync_user_repos()
        self.sync_user_organizations()

    def sync_user_repos(self):
        stored_repos = self.db_session.query(Repository.id).filter(
            Repository.owner_id == self.user.github_id,
            Repository.owner_type == 'user',
        )
        stored_repos = [repo.id for repo in stored_repos]
        saved_repos = []
        for github_repo in self.github.get_user().get_repos('public'):
            repo = self.save_repo(github_repo)
            saved_repos.append(repo.id)
        self.remove_unused_repos(stored_repos, saved_repos)

    def sync_user_organizations(self):
        self.drop_memberships()
        orgs = []
        for org in self.github.get_user().get_orgs():
            orgs.append(self.save_org(org))
        self.user.organizations = orgs
        self.db_session.flush()

    def save_org(self, github_org):
        org = self.db_session.query(Organization).filter_by(
            github_id=github_org.id,
        ).first()
        if org is None:
            org = Organization(
                github_id=github_org.id,
                login=github_org.login,
                name=github_org.name,
            )
            self.db_session.add(org)
            self.db_session.flush()
        else:
            org.login = github_org.login
            org.name = github_org.name
        self.sync_org_repos(org)
        return org

    def sync_org_repos(self, org):
        stored_repos = self.db_session.query(Repository.id).filter_by(
            owner_type='organization', owner_id=org.github_id,
        )
        stored_repos = [repo.id for repo in stored_repos]
        saved_repos = []
        github_organization = self.github.get_organization(org.login)
        github_repos = github_organization.get_repos('public')
        for github_repo in github_repos:
            repo = self.save_repo(github_repo)
            saved_repos.append(repo.id)
        self.remove_unused_repos(stored_repos, saved_repos)

    def drop_memberships(self):
        self.db_session.query(Membership).filter_by(
            user_id=self.user.id
        ).delete('fetch')
        self.db_session.flush()

    def save_repo(self, github_repo):
        repo = self.db_session.query(Repository.id).filter_by(
            github_id=github_repo.id,
        ).first()
        if repo is None:
            repo = Repository(
                github_id=github_repo.id,
                name=github_repo.name,
                url=github_repo.html_url,
                owner_name=github_repo.owner.login,
                owner_type=github_repo.owner.type.lower(),
                owner_id=github_repo.owner.id,
                token=generate_token(github_repo.id)
            )
            self.db_session.add(repo)
            self.db_session.flush()
        else:
            repo.name = github_repo.name
            repo.url = github_repo.html_url
            repo.owner_name = github_repo.owner.login
            repo.owner_type = github_repo.owner.type.lower()
            repo.owner_id = github_repo.owner.id
        self.save_repo_permissions(repo.id, github_repo.permissions)
        return repo

    def save_repo_permissions(self, repo_id, permissions):
        permission = self.db_session.query(Permission).filter_by(
            repository_id=repo_id, user_id=self.user.id
        ).first()
        if permission is None:
            permission = Permission(
                repository_id=repo_id,
                user_id=self.user.id,
                admin=permissions.admin,
                push=permissions.push,
                pull=permissions.pull,
            )
            self.db_session.add(permission)
            self.db_session.flush()
        else:
            permission.admin = permissions.admin
            permission.pull = permissions.pull
            permission.push = permissions.push

    def remove_unused_repos(self, stored_repos, saved_repos):
        difference = set(stored_repos) - set(saved_repos)
        if difference:
            self.db_session.query(Repository.id).filter(
                Repository.id.in_(difference)
            ).delete('fetch')
            self.db_session.flush()
