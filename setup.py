#!/usr/bin/env python
import os

from setuptools import setup, find_packages

import howdou

def read_md(f):
    with open(f, 'r', encoding='utf-8') as fin:
        return fin.read()

CURRENT_DIR = os.path.abspath(os.path.dirname(__file__))

def get_reqs(*fns):
    lst = []
    for fn in fns:
        with open(os.path.join(CURRENT_DIR, fn), encoding='utf-8') as fin:
            for package in fin.readlines():
                package = package.strip()
                if not package:
                    continue
                lst.append(package.strip())
    return lst

setup(
    name='howdou',
    version=howdou.__version__,
    description='Instant coding answers via the command line',
    long_description=read_md('README.md'),
    long_description_content_type='text/markdown',
    #https://pypi.python.org/pypi?%3Aaction=list_classifiers
    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'Environment :: Console',
        'Intended Audience :: Developers',
        'Programming Language :: Python :: 3.7',
        'Topic :: Documentation',
    ],
    keywords='howdou help console command line answer',
    author='Chris Spencer',
    author_email='chrisspen@gmail.com',
    url='https://github.com/chrisspen/howdou',
    license='MIT',
    packages=find_packages(),
    entry_points={
        'console_scripts': [
            'howdou = howdou.howdou:command_line_runner',
        ]
    },
    install_requires=get_reqs('requirements.txt'),
    tests_require=get_reqs('requirements-test.txt'),
)
