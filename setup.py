from setuptools import setup, find_packages


setup(
    name='alfred-sync',
    version='0.1.dev',
    license='ISC',
    description='Alfred github sync app',
    url='https://github.com/alfredhq/alfred-sync',
    author='Alfred Developers',
    author_email='team@alfredhq.com',
    packages=find_packages(),
    install_requires=[
        'SQLAlchemy',
        'PyYAML',
        'alfred-db',
        'requests',
    ],
)
