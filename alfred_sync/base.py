from alfred_db.session import Session
from alfred_db.models import User
from alfred_db.models.organization import Membership

from github import Github
from sqlalchemy import create_engine


class BaseHandler(object):

    @classmethod
    def dispatch(cls, config, task):
        engine = create_engine(config['database_uri'])
        db_session = Session(bind=engine)
        handler = cls(db_session, config, task)
        handler.run()

    def __init__(self, db_session, config, task):
        self.db_session = db_session
        self.config = config
        self.task = task
        self.user_id = self.task['user_id']
        self.user = self.db_session.query(User).get(self.user_id)
        self.github = Github(self.user.github_access_token)

    def run(self):
        raise NotImplementedError
