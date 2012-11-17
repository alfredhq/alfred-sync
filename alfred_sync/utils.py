import hashlib
import os
from random import choice


def generate_token(repo_id):
    random_base = os.urandom(32).encode('hex') + str(repo_id)
    base = ''.join(choice(random_base) for n in range(100))
    return hashlib.sha1(base).hexdigest()
