import requests
import json


class GithubException(Exception):
    pass


class Github(object):

    API_ROOT = 'https://api.github.com'

    def __init__(self, login, password=None, token=None):
        params = {}
        auth = None
        if token is not None:
            params['access_token'] = token
        elif login and password:
            auth = (login, password)
        else:
            params['access_token'] = login
        self.requester = requests.session(params=params, auth=auth)

    def get_full_url(self, url):
        return '{}{}'.format(self.API_ROOT, url)

    def get(self, url, **params):
        response = self.requester.get(
            self.get_full_url(url), params=params
        )
        if response.status_code == 200:
            return response.json
        raise GithubException(
            'GET {!r} returned status code {!r}'.format(
                self.get_full_url(url), response.status_code
            )
        )

    def post(self, url, data=None):
        if data is None:
            data = json.dumps(data)
        response = self.requester.post(
            self.get_full_url(),
            data=data,
        )
        if response.status_code == 201:
            return response.json
        raise GithubException(
            'POST {!r} returned status code {!r}'.format(
                self.get_full_url(url), response.status_code
            )
        )

    def user_resource_url(self, username=None):
        if username is None:
            return '/user'
        return '/users/{}'.format(username)

    def user(self, username=None):
        return self.get(self.user_resource_url(username))

    def user_repos(self, username=None, **params):
        resource_url = '{}/repos'.format(self.user_resource_url(username))
        return self.get(resource_url, **params)

    def user_organizations(self, username=None, **params):
        resource_url = '{}/orgs'.format(self.user_resource_url(username))
        return self.get(resource_url, **params)

    def organizations_repos(self, organization, **params):
        resource_url = '/orgs/{}/repos'.format(organization)
        return self.get(resource_url, **params)
