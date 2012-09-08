import requests
import json
from urllib.parse import parse_qs


class GithubException(Exception):
    pass


class Github(object):

    API_ROOT = 'https://api.github.com'

    def __init__(self, login, password=None, token=None):
        params = {
            'per_page': 100,
        }
        auth = None
        if token is not None:
            params['access_token'] = token
        elif login and password:
            auth = (login, password)
        else:
            params['access_token'] = login
        self.requester = requests.session(params=params, auth=auth)

    def parse_link_headers(self, link_header):
        links = {}
        for link in link_header.split(','):
            url, rel = link.split('; ')
            url = url[1:-1].split('?')[1]
            rel = rel[5:-1]
            links[rel] = parse_qs(url)
        return links

    def get_full_url(self, url):
        return '{}{}'.format(self.API_ROOT, url)

    def _get(self, url, **params):
        response = self.requester.get(url, params=params)
        if not response.status_code == 200:
            raise GithubException(
                'GET {!r} returned status code {!r}'.format(
                    self.get_full_url(url), response.status_code
                )
            )
        return response

    def _get_paginated_list(self, url, per_page, last_page):
        for page in range(1, last_page):
            items = self._get(url, per_page=per_page, page=page).json
            for item in items:
                yield item

    def get(self, url, **params):
        url = self.get_full_url(url)
        response = self._get(url, **params)
        if 'link' in response.headers:
            link_header = response.headers['link']
            links = self.parse_link_headers(link_header)
            last = links['last']
            last_page = int(last['page'][0])
            per_page = last['per_page']
            return self._get_paginated_list(url, per_page, last_page)
        return response.json

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

    def organization(self, login, **params):
        resource_url = '/orgs/{}'.format(login)
        return self.get(resource_url, **params)

    def organizations_repos(self, organization, **params):
        resource_url = '/orgs/{}/repos'.format(organization)
        return self.get(resource_url, **params)
