import mock
import unittest

from datetime import datetime

from alfred_db import Session
from alfred_db.models import Repository, User, Permission, Organization, Base

from alfred_sync.base import BaseHandler
from alfred_sync.handlers import SyncHandler, HooksHandler

from sqlalchemy import create_engine
from pretend import stub
from pytz import utc


engine = create_engine('sqlite:///:memory:')
Session.configure(bind=engine)


class BaseTestCase(unittest.TestCase):

    config = {
        'num_workers': 2,
        'database_uri': 'sqlite:///:memory:',
        'listener_url': 'http://listener.alfredhq.org',
    }

    def create_user(self):
        user = User(github_id=1000, github_access_token='token',
                    login='alfred', name='Alfred', email='alfred@alfred.org',
                    apitoken='superapitoken')
        self.session.add(user)
        self.session.commit()
        return user

    def setUp(self):
        self.session = Session()
        Base.metadata.create_all(engine)
        self.user = self.create_user()

    def tearDown(self):
        self.session.close()
        Base.metadata.drop_all(engine)


class BaseHandlerTestCase(BaseTestCase):

    @mock.patch('alfred_sync.base.BaseHandler.__init__')
    @mock.patch('alfred_sync.base.BaseHandler.run')
    def test_run_method(self, handler_run, handler_init):
        handler_init.return_value = None
        BaseHandler.dispatch(self.config, {'user_id': self.user.id})
        self.assertTrue(handler_init.called)
        self.assertTrue(handler_run.called)


class SyncHandlerTestCase(BaseTestCase):

    github_repo = stub(
        id=1000, name='test',
        owner=stub(
            login='xobb1t',
            type='User',
            id=3000
        ), html_url='https://github.com/xobb1t/test',
        permissions=stub(admin=True, push=False, pull=True)
    )
    github_organization = stub(id=2000, login='alfred', name='Alfred')

    def setUp(self):
        super(SyncHandlerTestCase, self).setUp()
        self.task = {'user_id': self.user.id}
        self.sync_handler = SyncHandler(self.session, self.config, self.task)
        self.github_patch = mock.patch('github.Github')
        self.Github = self.github_patch.start()
        self.sync_handler.github = self.Github()

    def tearDown(self):
        super(SyncHandlerTestCase, self).tearDown()
        self.github_patch.stop()

    def create_user(self):
        user = User(github_id=1000, github_access_token='token',
                    login='alfred', name='Alfred', email='alfred@alfred.org',
                    apitoken='superapitoken')
        self.session.add(user)
        self.session.commit()
        return user

    def create_repo(self):
        repo = Repository(
            github_id=1000, name='test',
            url='https://github.com/xobb1t/test',
            owner_name='xobb1t', owner_type='user',
            owner_id=self.user.github_id,
            token='repo-token'
        )
        self.session.add(repo)
        self.session.commit()
        return repo

    def create_organization(self):
        organization = Organization(
            github_id=2000, login='alfred', name='Alfred'
        )
        self.session.add(organization)
        self.session.commit()
        return organization

    def test_repo_save(self):
        repo = self.sync_handler.save_repo(self.github_repo)

        self.assertEqual(repo.github_id, 1000)
        self.assertEqual(repo.name, 'test')
        self.assertEqual(repo.url, 'https://github.com/xobb1t/test')
        self.assertEqual(repo.owner_name, 'xobb1t')
        self.assertEqual(repo.owner_type, 'user')
        self.assertEqual(repo.owner_id, 3000)
        self.assertEqual(repo.url, 'https://github.com/xobb1t/test')

        permissions = self.session.query(Permission).filter_by(
            repository_id=repo.id, user_id=self.user.id
        ).first()
        self.assertIsNotNone(permissions)
        self.assertTrue(permissions.admin)
        self.assertFalse(permissions.push)
        self.assertTrue(permissions.pull)

    def test_repo_updated(self):
        created_repo = self.create_repo()
        self.sync_handler.save_repo(self.github_repo)
        repo = self.sync_handler.save_repo(self.github_repo)
        count = self.session.query(Repository).count()
        self.assertEqual(count, 1)
        self.assertEqual(created_repo.id, repo.id)
        self.assertEqual(created_repo.token, 'repo-token')

    @mock.patch('alfred_sync.handlers.SyncHandler.sync_org_repos')
    def test_save_org(self, sync_org_repos):
        org = self.sync_handler.save_org(self.github_organization)
        self.assertEqual(org.github_id, 2000)
        self.assertEqual(org.name, 'Alfred')
        self.assertEqual(org.login, 'alfred')

    @mock.patch('alfred_sync.handlers.SyncHandler.sync_org_repos')
    def test_save_org_updated(self, sync_org_repos):
        created_org = self.create_organization()
        org = self.sync_handler.save_org(self.github_organization)
        self.assertEqual(created_org.id, org.id)

    @mock.patch('alfred_sync.handlers.SyncHandler.remove_unused_repos')
    @mock.patch('alfred_sync.handlers.SyncHandler.save_repo')
    def test_sync_org_repos(self, save_repo, remove_unused_repos):
        organization = self.create_organization()
        self.sync_handler.github.get_organization().get_repos.return_value = [
            self.github_repo
        ]
        repo = self.create_repo()
        save_repo.return_value = repo

        self.sync_handler.sync_org_repos(organization)
        save_repo.assert_has_calls([
            mock.call(self.github_repo)
        ])
        remove_unused_repos.assert_called_once_with([], [repo.id])

    @mock.patch('alfred_sync.handlers.SyncHandler.save_org')
    def test_sync_user_organizations(self, save_org):
        org = self.create_organization()
        save_org.return_value = org
        self.sync_handler.github.get_user().get_orgs.return_value = [0]

        self.sync_handler.sync_user_organizations()
        save_org.assert_has_calls([
            mock.call(0)
        ])
        self.assertEqual(self.user.organizations, [org])

    @mock.patch('alfred_sync.handlers.SyncHandler.remove_unused_repos')
    @mock.patch('alfred_sync.handlers.SyncHandler.save_repo')
    def test_sync_user_repos(self, save_repo, remove_unused_repos):
        self.sync_handler.github.get_user().get_repos.return_value = [
            self.github_repo
        ]
        repo = self.create_repo()
        save_repo.return_value = repo
        self.sync_handler.sync_user_repos()
        remove_unused_repos.assert_called_once_with([repo.id], [repo.id])

    @mock.patch('alfred_db.session.Session.rollback')
    @mock.patch('alfred_sync.handlers.SyncHandler.sync_user_repos')
    def test_rollback_on_exception(self, sync_user_repos, session_rollback):
        sync_user_repos.side_effect = TypeError
        with self.assertRaises(TypeError):
            self.sync_handler.run()
        self.assertTrue(session_rollback.called)

    @mock.patch('alfred_sync.handlers.SyncHandler.sync_user_repos')
    @mock.patch('alfred_sync.handlers.SyncHandler.set_user_syncing')
    def test_sync_status_changes(self, set_user_syncing, sync_user_repos):
        sync_user_repos.side_effect = TypeError
        with self.assertRaises(TypeError):
            self.sync_handler.run()
        set_user_syncing.assert_has_calls([
            mock.call(True),
            mock.call(False)
        ])

    @mock.patch('alfred_sync.handlers.SyncHandler.sync_user_repos')
    @mock.patch('alfred_sync.handlers.SyncHandler.sync_user_organizations')
    @mock.patch('alfred_sync.handlers.now')
    def test_user_last_synced_at_set(self, now, sync_user_organizations,
                                     sync_user_repos):
        now.return_value = datetime.utcnow().replace(tzinfo=utc)
        self.sync_handler.run()
        self.assertTrue(now.called)

    @mock.patch('alfred_sync.handlers.SyncHandler.sync_user_repos')
    @mock.patch('alfred_sync.handlers.now')
    def test_user_last_synced_with_exception(self, now, sync_user_repos):
        sync_user_repos.side_effect = TypeError
        with self.assertRaises(TypeError):
            self.sync_handler.run()
        self.assertFalse(now.called)

    @mock.patch('alfred_sync.handlers.SyncHandler.sync_user_repos')
    @mock.patch('alfred_sync.handlers.SyncHandler.sync_user_organizations')
    def test_user_already_synging(self, sync_user_organizations, sync_user_repos):
        self.user.is_syncing = True
        self.sync_handler.run()
        self.assertFalse(sync_user_repos.called)
        self.assertFalse(sync_user_organizations.called)
