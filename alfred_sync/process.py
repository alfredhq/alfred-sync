import logging
import multiprocessing
import os

import msgpack
import pika

from alfred_db.helpers import now
from alfred_db.models.organization import Membership, Organization
from alfred_db.models.permission import Permission
from alfred_db.models.repository import Repository
from alfred_db.models.user import User
from alfred_db.session import Session

from github import Github
from sqlalchemy import create_engine

from .utils import generate_token


class SyncProcess(multiprocessing.Process):

    def __init__(self, config):
        super(SyncProcess, self).__init__()
        self.config = config

    def run(self):
        logging.info(
            'Alfred-sync worker launched with pid: {!r}'.format(self.pid)
        )
        self.session = self.get_db_session(self.config['database_uri'])
        amqp_config = self.config['amqp']
        amqp_connection = self.get_amqp_connection(amqp_config['url'])
        amqp_channel = self.get_amqp_channel(
            amqp_connection, self.callback, amqp_config['queue_name']
        )
        try:
            amqp_channel.start_consuming()
        except Exception, e:
            raise e
        finally:
            self.session.close()

    def get_db_session(self, database_uri):
        engine = create_engine(database_uri)
        return Session(bind=engine)

    def get_amqp_connection(self, amqp_url):
        return pika.BlockingConnection(
            pika.URLParameters(amqp_url)
        )

    def get_amqp_channel(self, connection, callback, queue_name):
        channel = connection.channel()
        channel.queue_declare(queue=queue_name, durable=True)
        channel.basic_qos(prefetch_count=1)
        channel.basic_consume(callback, queue=queue_name)
        return channel

    def get_user(self, user_id):
        return self.session.query(User).filter_by(
            id=user_id, is_syncing=False
        ).first()

    def callback(self, ch, method, properties, body):
        task = msgpack.unpackb(body, encoding='utf-8')
        logging.info('[PID {}] Recieved task {!r}'.format(self.pid, task))
        self.user = self.get_user(task['user_id'])
        if not self.user:
            ch.basic_ack(delivery_tag=method.delivery_tag)
            logging.info(
                '[PID {}] User {} is already syncing or not exists'.format(
                    self.pid, self.user_id
                )
            )
            return
        self.github = Github(self.user.github_access_token)
        self.set_user_syncing(True)
        try:
            self.sync()
        except Exception, e:
            self.session.rollback()
        else:
            self.user.last_synced_at = now()
            self.session.commit()
        finally:
            ch.basic_ack(delivery_tag=method.delivery_tag)
            self.set_user_syncing(False)
            logging.info('[PID {}] Finished task {!r}'.format(self.pid, task))

    def sync(self):
        self.sync_user_repos()
        self.sync_user_organizations()

    def set_user_syncing(self, status):
        self.user.is_syncing = status
        self.session.commit()

    def sync_user_repos(self):
        stored_repos = self.session.query(Repository.id).filter(
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
        self.session.flush()

    def save_org(self, github_org):
        org = self.session.query(Organization).filter_by(
            github_id=github_org.id,
        ).first()
        if org is None:
            org = Organization(
                github_id=github_org.id,
                login=github_org.login,
                name=github_org.name,
            )
            self.session.add(org)
            self.session.flush()
        else:
            org.login = github_org.login
            org.name = github_org.name
        self.sync_org_repos(org)
        return org

    def sync_org_repos(self, org):
        stored_repos = self.session.query(Repository.id).filter_by(
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
        self.session.query(Membership).filter_by(
            user_id=self.user.id
        ).delete('fetch')
        self.session.flush()

    def save_repo(self, github_repo):
        repo = self.session.query(Repository.id).filter_by(
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
            self.session.add(repo)
            self.session.flush()
        else:
            repo.name = github_repo.name
            repo.url = github_repo.html_url
            repo.owner_name = github_repo.owner.login
            repo.owner_type = github_repo.owner.type.lower()
            repo.owner_id = github_repo.owner.id
        self.save_repo_permissions(repo.id, github_repo.permissions)
        return repo

    def save_repo_permissions(self, repo_id, permissions):
        permission = self.session.query(Permission).filter_by(
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
            self.session.add(permission)
            self.session.flush()
        else:
            permission.admin = permissions.admin
            permission.pull = permissions.pull
            permission.push = permissions.push

    def remove_unused_repos(self, stored_repos, saved_repos):
        difference = set(stored_repos) - set(saved_repos)
        if difference:
            self.session.query(Repository.id).filter(
                Repository.id.in_(difference)
            ).delete('fetch')
            self.session.flush()
