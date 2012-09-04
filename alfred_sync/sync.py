from sqlalchemy import create_engine

from alfred_db.session import Session
from alfred_db.models import User, Organization, Repository, Permission
from alfred_db.models.organization import Membership

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
            Repository.owner_id==self.user.github_id,
            Repository.owner_type=='user',
        )
        stored_repos = [repo.id for repo in stored_repos]
        saved_repos = []
        for repo in self.github.user_repos(type='owner'):
            saved_repos.append(self.save_repo(repo))
        self.remove_unused_repos(stored_repos, saved_repos)

    def sync_user_organizations(self):
        self.drop_memberships()
        orgs = []
        for org in self.github.user_organizations():
            orgs.append(self.save_org(org))
        self.user.organizations = orgs
        self.db_session.flush()

    def save_org(self, gh_org):
        data = self.github.organization(gh_org['login'])
        org = self.db_session.query(Organization).filter_by(
            github_id=data['id'],
        ).first()
        if org is None:
            org = Organization(
                github_id=data['id'],
                login=data['login'],
                name=data['name'],
            )
            self.db_session.add(org)
            self.db_session.flush()
        else:
            org.login = data['login']
            org.name = data['name']
        self.sync_org_repos(org)
        return org

    def sync_org_repos(self, org):
        stored_repos = self.db_session.query(Repository.id).filter_by(
            owner_type='organization', owner_id=org.github_id,
        )
        stored_repos = [repo.id for repo in stored_repos]
        saved_repos = []
        for repo in self.github.organizations_repos(org.login):
            saved_repos.append(self.save_repo(repo))
        self.remove_unused_repos(stored_repos, saved_repos)

    def drop_memberships(self):
        self.db_session.query(Membership).filter_by(
            user_id=self.user.id
        ).delete('fetch')
        self.db_session.flush()

    def save_repo(self, data):
        owner_data = self.github.user(data['owner']['login'])
        repo = self.db_session.query(Repository.id).filter_by(
            github_id=data['id'],
        ).first()
        if repo is None:
            repo = Repository(
                github_id=data['id'],
                name=data['name'],
                url=data['html_url'],
                owner_name=owner_data['login'],
                owner_type=owner_data['type'].lower(),
                owner_id=owner_data['id']
            )
            self.db_session.add(repo)
            self.db_session.flush()
        else:
            repo.name = data['name']
            repo.url = data['html_url']
            repo.owner_name = owner_data['login']
            repo.owner_type = owner_data['type'].lower()
            repo.owner_id = owner_data['id']
        self.save_repo_permissions(repo.id, data['permissions'])
        return repo.id

    def save_repo_permissions(self, repo_id, data):
        permission = self.db_session.query(Permission).filter_by(
            repository_id=repo_id, user_id=self.user.id
        ).first()
        if permission is None:
            permission = Permission(
                repository_id=repo_id,
                user_id=self.user.id,
                admin=data['admin'],
                push=data['push'],
                pull=data['pull'],
            )
            self.db_session.add(permission)
            self.db_session.flush()
        else:
            permission.admin = data['admin']
            permission.push = data['push']
            permission.pull = data['pull']

    def remove_unused_repos(self, stored_repos, saved_repos):
        difference = set(stored_repos) - set(saved_repos)
        if difference:
            self.db_session.query(Repository.id).filter(
                Repository.id.in_(difference)
            ).delete('fetch')
            self.db_session.flush()
