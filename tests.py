import mock
import unittest
import msgpack
import pika

from alfred_db import Session
from alfred_db.models import Repository, User, Permission, Organization, Base

from alfred_sync.process import SyncProcess

from sqlalchemy import create_engine
from pretend import stub


engine = create_engine('sqlite:///:memory:')
Session.configure(bind=engine)


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

config = {
    'num_workers': 2,
    'database_uri': 'sqlite:///:memory:',
    'amqp': {
        'url': 'amqp://guest:guest@localhost:5678/%2F',
        'queue_name': 'test_sync',
    }
}


def create_user():
    user = User(github_id=1000, github_access_token='token',
                login='alfred', name='Alfred', email='alfred@alfred.org',
                apitoken='superapitoken')
    session = Session()
    session.add(user)
    session.commit()
    try:
        return user.id
    finally:
        session.close()


def create_repo():
    repo = Repository(
        github_id=1000, name='test',
        url='https://github.com/xobb1t/test',
        owner_name='xobb1t', owner_type='user',
        owner_id=1000,
        token='repo-token'
    )
    session = Session()
    session.add(repo)
    session.commit()
    try:
        return repo.id
    finally:
        session.close()


class BaseHandlerTestCase(unittest.TestCase):

    def setUp(self):
        self.BlockingConnection = mock.Mock()
        self.connection = self.BlockingConnection.return_value
        self.channel = self.connection.channel.return_value
        self.connection_patch = mock.patch(
            'pika.BlockingConnection', self.BlockingConnection
        )
        self.connection_patch.start()

        self.github_patch = mock.patch('github.Github')
        Github = self.github_patch.start()
        self.github = Github()

        self.session = Session()
        Base.metadata.create_all(engine)
        self.user_id = create_user()
        self.user = self.session.query(User).get(self.user_id)

        self.task = {'user_id': self.user.id}
        self.sync_process = SyncProcess(config)
        self.sync_process.session = self.session
        self.sync_process.user = self.user
        self.sync_process.github = self.github

    def tearDown(self):
        self.session.close()
        Base.metadata.drop_all(engine)
        self.github_patch.stop()
        self.connection.patch.stop()

    @mock.patch('alfred_sync.process.SyncProcess.sync_user_organizations')
    @mock.patch('alfred_sync.process.SyncProcess.sync_user_repos')
    def test_sync(self, sync_user_repos, sync_user_organizations):
        self.sync_process.sync()
        self.assertTrue(sync_user_repos.called)
        self.assertTrue(sync_user_organizations.called)

    @mock.patch('alfred_sync.process.SyncProcess.save_repo')
    @mock.patch('alfred_sync.process.SyncProcess.remove_unused_repos')
    def test_sync_user_repos(self, remove_unused_repos, save_repo):
        self.github.get_user().get_repos.return_value = [
            github_repo
        ]
        save_repo.return_value = stub(id=1000)
        self.sync_process.sync_user_repos()
        save_repo.assert_called_once_with(github_repo)
        remove_unused_repos.assert_called_once_with([], [1000])

    @mock.patch('alfred_sync.process.SyncProcess.drop_memberships')
    def test_sync_user_organizations(self, drop_memberships):
        self.github.get_user().get_orgs.return_value = [
            github_organization
        ]
        self.sync_process.sync_user_organizations()
        self.assertEqual(drop_memberships.call_count, 1)
        self.assertEqual(len(self.user.organizations), 1)
        self.sync_process.sync_user_organizations()
        self.assertEqual(drop_memberships.call_count, 2)
        self.assertEqual(len(self.user.organizations), 1)

    def test_repo_save(self):
        repo = self.sync_process.save_repo(github_repo)

        self.assertEqual(repo.github_id, github_repo.id)
        self.assertEqual(repo.name, github_repo.name)
        self.assertEqual(repo.url, github_repo.html_url)
        self.assertEqual(repo.owner_name, github_repo.owner.login)
        self.assertEqual(repo.owner_type, github_repo.owner.type.lower())
        self.assertEqual(repo.owner_id, github_repo.owner.id)

        permissions = self.session.query(Permission).filter_by(
            repository_id=repo.id, user_id=self.user_id
        ).first()
        self.assertIsNotNone(permissions)
        self.assertEqual(permissions.admin, github_repo.permissions.admin)
        self.assertEqual(permissions.push, github_repo.permissions.push)
        self.assertEqual(permissions.pull, github_repo.permissions.pull)

        resaved_repo = self.sync_process.save_repo(github_repo)
        self.assertEqual(resaved_repo.id, repo.id)

    @mock.patch('alfred_sync.process.SyncProcess.sync_org_repos')
    def test_org_save(self, sync_org_repos):
        org = self.sync_process.save_org(github_organization)
        self.assertEqual(org.github_id, github_organization.id)
        self.assertEqual(org.login, github_organization.login)
        self.assertEqual(org.name, github_organization.name)
        sync_org_repos.assert_has_calls([mock.call(org)])

    def test_set_user_syncing(self):
        self.sync_process.set_user_syncing(True)
        self.assertTrue(self.user.is_syncing)
        self.sync_process.set_user_syncing(False)
        self.assertFalse(self.user.is_syncing)

    def test_run(self):
        self.sync_process.run()
        amqp_config = self.sync_process.config['amqp']
        self.channel.queue_declare.assert_has_calls([
            mock.call(queue=amqp_config['queue_name'], durable=True)
        ])
        self.channel.basic_qos.assert_has_calls([
            mock.call(prefetch_count=1)
        ])
        self.channel.basic_consume.assert_has_calls([
            mock.call(self.sync_process.callback, queue=amqp_config['queue_name'])
        ])
        self.channel.start_consuming.assert_has_calls([
            mock.call(),
        ])

    @mock.patch('alfred_sync.process.SyncProcess.set_user_syncing')
    @mock.patch('alfred_sync.process.SyncProcess.sync')
    def test_callback(self, sync, set_user_syncing):
        message_method = mock.Mock()
        message_method.delivery_tag = 'TAG'
        body = msgpack.packb(self.task, encoding='utf-8')
        self.sync_process.callback(self.channel, message_method, None, body)
        sync.assert_has_calls([mock.call()])
        self.channel.basic_ack.assert_has_calls([mock.call(delivery_tag='TAG')])
        set_user_syncing.assert_has_calls([
            mock.call(True),
            mock.call(False),
        ])
