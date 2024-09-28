#!/usr/bin/env python
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import os
import sys
import unittest
from unittest import TestCase as _TestCase
from time import sleep
from random import randint
from pprint import pprint
import traceback

import yaml

from . import howdou
from .howdou import HowDoU, get_parser

# We throttle our queries, since they touch Google and if we go too fast Google starts throwing 404 errors.
def random_wait():
    sleep(randint(2, 6))

def _getattribute(cls, self, attrname):

    # Wrap test methods so we can capture their exceptions.
    # The default unittest framework doesn't make this easy.

    def test_wrap(func):
        def _wrap(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                print('!'*80, file=sys.stderr)
                print(f'An exception was encountered in test method {self._testMethodName}.', file=sys.stderr)
                traceback.print_exc(file=sys.stderr)
                print('!'*80, file=sys.stderr)
                raise
        return _wrap

    attr = super(cls, self).__getattribute__(attrname)

    if attrname.startswith('test') and callable(attr):
        attr = test_wrap(attr)

    return attr

class TestCase(_TestCase):

    test_name_fout = sys.stderr

    test_name_format = '\n{bar}\nRunning test: {name}\n{bar}\n'

    def setUp(self):
        # Always print the current test name before the test.
        kwargs = dict(
            bar='#'*80,
            name=self._testMethodName,
        )
        print(self.test_name_format.format(**kwargs), file=self.test_name_fout)
        super().setUp()

class HowdouTestCase(TestCase):

    def call_howdou(self, query):
        parser = get_parser()
        args = vars(parser.parse_args(query.split(' ')))
        ret = HowDoU(**args).run()
        return ret

    def setUp(self):
        super().setUp()

        self.queries = [
            'format date bash',
            'print stack trace python',
            'convert mp4 to animated gif',
            'create tar archive',
        ]
        self.pt_queries = [
            'abrir arquivo em python',
            'enviar email em django',
            'hello world em c',
        ]
        self.bad_queries = [
            'moe',
            'mel',
        ]

        # Define temporary locations for all persistent data files
        # so we don't corrupt any production system.
        howdou.KNOWLEDGEBASE_INDEX = 'howdou-test'
        howdou.KNOWLEDGEBASE_FN = '/tmp/.howdou.yml'
        howdou.KNOWLEDGEBASE_TIMESTAMP_FN = '/tmp/.howdou_last'
        howdou.LOCKFILE_PATH = '/tmp/.howdou_test_lock'
        howdou.APP_DATA_DIR = '/tmp/.howdou'
        # Purge these temporary files incase we've run these tests before
        # and there are lingering partial files.
        delete_paths = [
            howdou.KNOWLEDGEBASE_FN,
            howdou.KNOWLEDGEBASE_TIMESTAMP_FN,
            howdou.LOCKFILE_PATH,
            howdou.APP_DATA_DIR,
        ]
        for path in delete_paths:
            try:
                os.system(f'rm -Rf "{path}"')
            except OSError:#FileNotFoundError:
                pass

        # Create a stub howdou objects for reference.
        parser = get_parser()
        args = vars(parser.parse_args([' ']))
        self.howdou = HowDoU(**args)
        # Purge the test index from the server to ensure we're starting fresh.
        self.howdou.delete_index()

    def tearDown(self):
        pass

    def test_find_true_link(self):
        s = '/url?q=http://stackoverflow.com/questions/11004721/how-to-do-i-convert-an-animated-gif-to-an-mp4-or-mv4-on-the-command-line&sa=U'
        ret = howdou.find_true_link(s)
        self.assertEqual(ret, 'http://stackoverflow.com/questions/11004721/how-to-do-i-convert-an-animated-gif-to-an-mp4-or-mv4-on-the-command-line&sa=U')

    def test_get_link_at_pos(self):
        self.assertEqual(howdou.get_link_at_pos(['/questions/42/'], 1), '/questions/42/')
        self.assertEqual(howdou.get_link_at_pos(['/questions/42/'], 2), '/questions/42/')
        self.assertEqual(howdou.get_link_at_pos(['/howdou', '/questions/42/'], 1), '/questions/42/')
        self.assertEqual(howdou.get_link_at_pos(['/howdou', '/questions/42/'], 2), '/questions/42/')
        self.assertEqual(howdou.get_link_at_pos(['/questions/42/', '/questions/142/'], 1), '/questions/42/')

    def test_answers(self):
        for query in self.queries:
            self.assertTrue(self.call_howdou(query))
            random_wait()
        for query in self.bad_queries:
            self.assertTrue(self.call_howdou(query))
            random_wait()

        os.environ['HOWDOU_LOCALIZATION'] = 'pt-br'
        for query in self.pt_queries:
            self.assertTrue(self.call_howdou(query))
            random_wait()

    def test_answer_links(self):
        for query in self.queries:
            print('query:', query)
            ret = self.call_howdou(query + ' -l')
            print('ret:', ret)
            self.assertTrue('https://' in ret)
            random_wait()

    def test_position(self):
        query = self.queries[0]
        first_answer = self.call_howdou(query)
        second_answer = self.call_howdou(query + ' -p2')
        self.assertNotEqual(first_answer, second_answer)

    def test_all_text(self):
        query = self.queries[0]
        first_answer = self.call_howdou(query)
        print('first_answer:', first_answer)
        second_answer = self.call_howdou(query + ' -a')
        print('second_answer:', second_answer)
        self.assertNotEqual(first_answer, second_answer)
        self.assertTrue("Answer from https://stackoverflow.com" in second_answer)

    def test_multiple_answers(self):
        query = self.queries[0]
        first_answer = self.call_howdou(query)
        second_answer = self.call_howdou(query + ' -n3')
        self.assertNotEqual(first_answer, second_answer)

    def test_unicode_answer(self):
        assert self.call_howdou('make a log scale d3')
        assert self.call_howdou('python unittest -n3')
        assert self.call_howdou('parse html regex -a')
        assert self.call_howdou('delete remote git branch -a')

    def test_local_cache_index(self):

#         args = vars(parser.parse_args(query.split(' ')))
#         hdu = HowDoU(**args).run()

        # Create a seed knowledge base.
        self.howdou.init_kb()
        self.assertTrue(os.path.isfile(howdou.KNOWLEDGEBASE_FN))
        os.system('cat %s' % howdou.KNOWLEDGEBASE_FN)

        # Confirm there's nothing yet indexed in our knowledge base.
        self.assertFalse(self.howdou.is_indexed(
            'how do I create a new howdou knowledge base entry',
            'nano ~/.howdou.yml\nhowdou --reindex',
        ))

        # Re-index the knowledge base file.
        self.howdou.verbose = True
        self.howdou.reindex()

        # Search the index.
        self.howdou.ignore_local = False
        self.howdou.ignore_remote = True
        ret = self.howdou.ask(q='how do I create a new howdou knowledge base entry', output=False)
        print('ret:')
        pprint(ret, indent=4)
        self.assertEqual(len(ret), 1)
        self.assertEqual(ret[0]['answer'], 'nano ~/.howdou.yml\nhowdou --reindex')

        # Add a new item that should not conflict with the existing one.
        item = yaml.load('''
questions:
-   how many toads can a pickle tickle
tags:
-   context:
answers:
-   weight: 1
    date: 2017-2-1
    source:
    tags:
    formatter: nl
    action_subject: []
    text: |-
        twice as many as a canary
''')
        print('item:', item)
        self.howdou.add_item(item)
        print('kb:', self.howdou.kb_filename)
        print(open(self.howdou.kb_filename).read())
        self.howdou.reindex()
        self.assertEqual(self.howdou.last_reindex_count, 2)

        # Ask the same question as before and ensure the result hasn't changed.
        ret = self.howdou.ask(q='how do I create a new howdou knowledge base entry', output=False)
        print('ret:')
        pprint(ret, indent=4)
        self.assertEqual(len(ret), 1)
        self.assertEqual(ret[0]['answer'], 'nano ~/.howdou.yml\nhowdou --reindex')

        # Ask a new question that should match the new entry and confirm a new result.
        ret = self.howdou.ask(q='how many toads can a pickle tickle', output=False)
        print('ret:')
        pprint(ret, indent=4)
        self.assertEqual(len(ret), 1)
        self.assertEqual(ret[0]['answer'], 'twice as many as a canary')

        # Add a new item that should conflict with an existing one, but still not skew results.
        item = yaml.load('''
questions:
-   how do I delete a howdou knowledge base entry
tags:
-   context:
answers:
-   weight: 1
    date: 2017-2-1
    source:
    tags:
    formatter: nl
    action_subject: []
    text: |-
        1. open .howdou.yml
        2. find entry
        3. delete entry
        4. that's it
''')
        print('item:', item)
        self.howdou.add_item(item)
        self.howdou.reindex()
        self.assertEqual(self.howdou.last_reindex_count, 3)

        # Ask our original question and confirm the original result.
        ret = self.howdou.ask(q='how do I create a new howdou knowledge base entry', output=False)
        print('ret:')
        pprint(ret, indent=4)
        self.assertEqual(len(ret), 1)
        self.assertEqual(ret[0]['answer'], 'nano ~/.howdou.yml\nhowdou --reindex')

        # Ask a question that should find the new entry.
        ret = self.howdou.ask(q='how do I delete a howdou knowledge base entry', output=False)
        print('ret:')
        pprint(ret, indent=4)
        self.assertEqual(len(ret), 1)
        self.assertEqual(ret[0]['answer'], '1. open .howdou.yml\n2. find entry\n3. delete entry\n4. that\'s it')

class HowdouTestCaseEnvProxies(TestCase):

    def setUp(self):
        super(HowdouTestCaseEnvProxies, self).setUp()
        self.temp_get_proxies = howdou.getproxies

    def tearDown(self):
        howdou.getproxies = self.temp_get_proxies

    def test_get_proxies1(self):
        def getproxies1():
            proxies = {'http': 'wwwproxy.company.com',
                       'https': 'wwwproxy.company.com',
                       'ftp': 'ftpproxy.company.com'}
            return proxies

        howdou.getproxies = getproxies1
        filtered_proxies = howdou.get_proxies()
        self.assertTrue('http://' in filtered_proxies['http'])
        self.assertTrue('http://' in filtered_proxies['https'])
        self.assertTrue('ftp' not in filtered_proxies.keys())


if __name__ == '__main__':
    unittest.main()
