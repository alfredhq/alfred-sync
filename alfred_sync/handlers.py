from alfred_db.helpers import now
from alfred_db.models.organization import Membership, Organization
from alfred_db.models.permission import Permission
from alfred_db.models.repository import Repository

from .base import BaseHandler
from .utils import generate_token


class SyncHandler(BaseHandler):

    def run(self):
        if self.user.is_syncing:
            return
        self.set_user_syncing(True)
        try:
            self.sync_user_repos()
            self.sync_user_organizations()
        except Exception, e:
            self.db_session.rollback()
            raise e
        else:
            self.user.last_synced_at = now()
            self.db_session.commit()
        finally:
            self.set_user_syncing(False)
            self.db_session.close()

    def set_user_syncing(self, status):
        self.user.is_syncing = status
        self.db_session.commit()

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
