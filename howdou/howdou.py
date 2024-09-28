#!/usr/bin/env python
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

# Suppress ElasticsearchWarnings
import warnings
warnings.filterwarnings("ignore")

# pylint: disable=wrong-import-position
import argparse
import datetime
import os
import re
import sys
import hashlib
import traceback
from pprint import pprint
try:
    from commands import getoutput
except ImportError:
    from subprocess import getoutput
from collections import defaultdict

import requests
from requests.exceptions import SSLError

import yaml

import dateutil.parser

from fake_useragent import UserAgent

from pygments import highlight
from pygments.lexers import guess_lexer, get_lexer_by_name
from pygments.formatters import TerminalFormatter # pylint: disable=no-name-in-module
from pygments.util import ClassNotFound

try:
    from urllib.parse import quote as url_quote
except ImportError:
    from urllib import quote as url_quote

try:
    from urllib import getproxies
except ImportError:
    from urllib.request import getproxies

import fasteners

from pyquery import PyQuery as pq

from elasticsearch import Elasticsearch

from .__init__ import __version__

LOCAL = 'local'
REMOTE = 'remote'

KNOWLEDGEBASE_FN = os.path.expanduser(os.getenv('HOWDOU_KB', '~/.howdou.yml'))
KNOWLEDGEBASE_INDEX = os.getenv('HOWDOU_INDEX', 'howdou')
KNOWLEDGEBASE_TIMESTAMP_FN = os.path.expanduser(os.getenv('HOWDOU_TIMESTAMP', '~/.howdou_last'))
APP_DATA_DIR = os.path.expanduser(os.getenv('HOWDOU_DIR', '~/.howdou'))
LOCKFILE_PATH = os.path.expanduser(os.getenv('HOWDOU_LOCKFILE', '~/.howdou_lock'))
CACHE_DIR = os.path.join(os.path.join(os.path.expanduser('~'), '.cache'), 'howdou')

KNOWLEDGEBASE_STUB = '''-   questions:
    -   how do I create a new howdou knowledge base entry
    tags:
        context: howdou
    answers:
    -   weight: 1
        date: 2014-2-22
        source:
        formatter:
        text: |
            nano ~/.howdou.yml
            howdou --reindex
'''

if os.getenv('HOWDOU_DISABLE_SSL'): # Set http instead of https
    SEARCH_URL = 'http://www.google.com/search?q=site:{0}%20{1}'
else:
    SEARCH_URL = 'https://www.google.com/search?q=site:{0}%20{1}'

LOCALIZATION = os.getenv('HOWDOU_LOCALIZATION') or 'en'

LOCALIZATON_URLS = {
    'en': 'stackoverflow.com',
    'pt-br': 'pt.stackoverflow.com',
}

ANSWER_HEADER = '--- Answer: {i} --- Weight: {weight} --- Source: {source} ---\n\n{answer}'

NO_ANSWER_MSG = '< no answer given >'

QUERY = 'query'
REINDEX = 'reindex'
CLEAR_CACHE = 'clear-cache'
SUMMARIZE_FIELD = 'summarize-field'
FILTER_BY_FIELD = 'filter-by-field'
ACTIONS = (QUERY, REINDEX, CLEAR_CACHE, SUMMARIZE_FIELD, FILTER_BY_FIELD)

DEFAULT_USERAGENT = 'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:51.0) Gecko/20100101 Firefox/51.0'

ua = UserAgent(fallback=DEFAULT_USERAGENT)

# Force dictionaries to be serialized in multi-line format.
def _represent_dictorder(self, data):
    _data = []
    ordering = ['questions', 'tags', 'answers', 'weight', 'date', 'source', 'formatter', 'action_subject', 'text']
    for key in ordering:
        if key in data:
            _data.append((str(key), data.pop(key)))
    if data:
        _data.extend(data.items())
    return self.represent_mapping('tag:yaml.org,2002:map', _data)

def _selective_representer(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|" if "\n" in data else None)

yaml.add_representer(str, _selective_representer)
yaml.add_representer(dict, _represent_dictorder)

def get_nested_key(element, keys):
    """
    Returns the key at the nested position inside element, which should be a nested dictionary.
    """
    keys = list(keys)
    if not keys:
        return element
    key = keys.pop(0)
    if key not in element:
        return
    return get_nested_key(element[key], keys)

def get_link_at_pos(links, position):

    def is_question(link):
        return re.search(r'questions/\d+/', link)

    links = [link for link in links if is_question(link)]
    if not links:
        return False

    if len(links) >= position:
        link = links[position-1]
    else:
        link = links[-1]
    return link

def touch(fname, times=None):
    with open(fname, 'a', encoding='utf-8'):
        os.utime(fname, times)

def get_text_hash(text):
    """
    Returns the hash of the given text.
    """
    h = hashlib.sha512()
    if not isinstance(text, str):
        text = str(text, encoding='utf-8', errors='replace')
    h.update(text.encode('utf-8', 'replace'))
    return h.hexdigest()

def get_proxies():
    proxies = getproxies()
    filtered_proxies = {}
    for key, value in proxies.items():
        if key.startswith('http'):
            if not value.startswith('http'):
                filtered_proxies[key] = f'http://{value}'
            else:
                filtered_proxies[key] = value
    return filtered_proxies

def find_true_link(s):
    """
    Sometimes Google wraps our links inside sneaky tracking links, which often fail and slow us down
    so remove them.
    """
    # Convert "/url?q=<real_url>" to "<real_url>".
    if s and s.startswith('/') and 'http' in s:
        s = s[s.find('http'):]
    return s

class HowDoU():

    def __init__(self, **kwargs):
        kwargs.setdefault('verbose', False)
        self.__dict__.update(kwargs)
        assert self.action in ACTIONS, f'Invalid action "{self.action}". Must be one of {", ".join(ACTIONS)}'
        self.cache_file = os.path.join(self.cache_dir, 'cache')
        self.query = (' '.join(self.query).replace('?', '')).strip()
        self.kb_filename = os.path.expanduser(self.kb_filename)
        self.kb_timestamp = os.path.expanduser(self.kb_timestamp)
        self.kb_app_dir = os.path.expanduser(self.kb_app_dir)
        self.append_header = False
        self.last_reindex_count = 0

    def get_es(self):
        return  Elasticsearch(
            hosts=[{
                'host': 'localhost',
                'port': 9200,
                'scheme': 'http'
            }],
            verify_certs=False,
            ssl_show_warn=False,
            request_timeout=30,
        )

    def delete_index(self):
        """
        Forcibly deletes the index from the server.
        """
        print(f'Deleting index {self.kb_index_name}...')
        es = self.get_es()
        es.indices.delete(index=self.kb_index_name, ignore=[400, 404])
        print(f'Deleting index cache at {self.kb_app_dir}...')
        os.system(f'rm -Rf {self.kb_app_dir}/*')

    def is_kb_updated(self):
        """
        Returns true if the knowledge base file has changed since the last run.
        """
        if not os.path.isfile(self.kb_timestamp):
            print('First-time indexing required.')
            return True
        kb_filenames = list(self.iter_kb(only_filenames=True))
        for kb_filename in kb_filenames:
            kb_last_modified = datetime.datetime.fromtimestamp(os.path.getmtime(kb_filename))
            timestamp_last_modified = datetime.datetime.fromtimestamp(os.path.getmtime(self.kb_timestamp))
            if kb_last_modified > timestamp_last_modified:
                print('Changes found.')
                return True
        return False

    def update_kb_timestamp(self):
        touch(self.kb_timestamp)

    def get_result(self, url):
        try:
            return requests.get(url, headers={'User-Agent': ua.random}, proxies=get_proxies(), timeout=30).text
        except SSLError as e:
            print('[ERROR] Encountered an SSL Error. Try using HTTP instead of '
                  'HTTPS by setting the environment variable "HOWDOU_DISABLE_SSL".\n')
            raise e

    def get_links(self, query):
        localization_url = LOCALIZATON_URLS[self.lang]
        result = self.get_result(SEARCH_URL.format(localization_url, url_quote(query)))
        html = pq(result)
        return [a.attrib['href'] for a in html('.l')] or [a.attrib['href'] for a in html('.r')('a')]

    def format_output(self, code):
        if not self.color:
            return code
        lexer = None

        # try to find a lexer using the StackOverflow tags
        # or the query arguments
        for keyword in self.query.split() + self.tags:
            try:
                lexer = get_lexer_by_name(keyword)
                break
            except ClassNotFound:
                pass

        # no lexer found above, use the guesser
        if not lexer:
            lexer = guess_lexer(code)

        return highlight(code, lexer, TerminalFormatter(bg='dark'))

    def get_answer(self, links):
        """
        Given search arguments and a links of web links (usually Stackoverflow),
        find the best answer to the search question.
        """
        #print('get_answer: args:', args, 'links:', links)
        link = get_link_at_pos(links, self.pos)
        if not link:
            return False, None

        # Don't lookup answer text, just return link.
        if self.link:
            return None, link

        page = self.get_result(find_true_link(link) + '?answertab=votes')
        html = pq(page)

        first_answer = html('.answer').eq(0)
        instructions = first_answer.find('pre') or first_answer.find('code')
        self.tags = [t.text for t in html('.post-tag')]

        if not instructions and not self.all:
            text = first_answer.find('.post-text').eq(0).text()
        elif self.all:
            texts = []
            for html_tag in first_answer.items('.post-text > *'):
                current_text = html_tag.text()
                if current_text:
                    if html_tag[0].tag in ['pre', 'code']:
                        texts.append(self.format_output(current_text))
                    else:
                        texts.append(current_text)
            texts.append(f'\n---\nAnswer from {link}')
            text = '\n'.join(texts)
        else:
            text = self.format_output(instructions.eq(0).text())
        if text is None:
            text = NO_ANSWER_MSG
        text = text.strip()
        return text, link

    def run_clear_cache(self):
        self.clear_cache()

    def clear_cache(self):
        self.delete_index()

    def init_kb(self):
        if not os.path.isfile(self.kb_filename):
            with open(self.kb_filename, 'w', encoding='utf-8') as fout:
                fout.write(KNOWLEDGEBASE_STUB)

    def mark_indexed(self, question_str, answer_str):
        hash_fn = os.path.join(self.kb_app_dir, get_text_hash(question_str))
        hash_contents = get_text_hash(answer_str)
        with open(hash_fn, 'w', encoding='utf-8') as fout:
            fout.write(hash_contents)

    def is_indexed(self, question_str, answer_str):
        """
        Returns true if this exact combination has been previously indexed.
        Returns false otherwise.
        """
        hash_fn = os.path.join(self.kb_app_dir, get_text_hash(question_str))
        if not os.path.isfile(hash_fn):
            return False
        hash_contents = get_text_hash(answer_str)
        with open(hash_fn, encoding='utf-8') as fin:
            if fin.read() != hash_contents:
                return False
        return True

    def add_item(self, item):
        """
        Dynamically appends an item to the knowledge base.
        Warning, this will overwrite the file!
        """
        assert isinstance(item, dict)
        assert 'questions' in item
        assert 'answers' in item and item['answers']
        for answer in item['answers']:
            assert 'date' in answer
            assert 'text' in answer
            answer.setdefault('weight', 1.0)
        item_str = yaml.dump([item], indent=4, default_flow_style=False)#, default_style='|')
        self.init_kb()
        with open(self.kb_filename, 'a', encoding='utf-8') as fout:
            fout.write(item_str)

    def show_gui_error(self, message, detail):
        getoutput(f'export DISPLAY=:0; notify-send "{message}" "{detail}"')

    def count_total_kb_entries(self, fn=None):
        cnt = 0
        for item in self.iter_kb(fn=None):
            cnt += 1
        return cnt

    def count_total_kb_answers(self, fn=None):
        cnt = 0
        for item in self.iter_kb(fn=None):
            if 'answers' in item:
                cnt += len(item['answers'])
        return cnt

    def iter_kb(self, fn=None, only_filenames=False):
        """
        Iterates over all knowledgebase entries.
        """
        if not os.path.isdir(self.kb_app_dir):
            os.mkdir(self.kb_app_dir)
        if only_filenames and fn is None:
            yield self.kb_filename
        fn = fn or self.kb_filename
        try:
            print(f'Loading knowledgebase file {fn}.')
            with open(fn, encoding='utf-8') as fin:
                for item in yaml.load(fin, Loader=yaml.FullLoader):
                    if isinstance(item, dict) and 'include' in item:
                        # Handle special "include" entries that direct us to load an additional file.
                        if only_filenames:
                            yield item['include']
                        else:
                            yield from self.iter_kb(item['include'])
                    else:
                        # Otherwise, yield normal entry.
                        # Dynamically add filename so it can be indexed and included in search results.
                        item['filename'] = fn
                        yield item
        except TypeError:
            pass

    def index_kb(self):
        """
        Processes all knowledgebase entries and enters them into the text search database.
        """
        es = self.get_es()
        count = 0

        if not os.path.isdir(self.kb_app_dir):
            os.mkdir(self.kb_app_dir)

        if self.force:
            self.delete_index()
        elif not self.is_kb_updated():
            print('No changes detected.')
            return

        # Count total combinations so we can accurately measure progress.
        self.vprint('kb_filename:', self.kb_filename)
        try:
            total = self.count_total_kb_answers()
        except yaml.scanner.ScannerError as exc:
            traceback.print_exc()
            self.show_gui_error('HowDoU Re-Indexing Error', exc)
            sys.exit(1)

        for item in self.iter_kb(self.kb_filename):

            # Combine the list of separate questions into a single text block.
            questions = '\n'.join(map(str, item.get('questions') or []))
            self.vprint('questions:', questions)
            if not questions:
                print('Skipping due to missing questions.')
                continue

            for answer in item['answers']:
                count += 1
                sys.stdout.write(f'\rRe-indexing {count} of {total}...')
                sys.stdout.flush()

                if not self.force and self.is_indexed(questions, answer['text']):
                    continue

                weight = float(answer.get('weight', 1))
                dt = answer['date']
                if isinstance(dt, str):
                    dt = dateutil.parser.parse(dt)

                text = questions + ' ' + answer['text']

                _id = get_text_hash(text)

                doc = dict(
                    questions=questions,
                    answer=answer['text'],
                    source=answer.get('source', ''),
                    filename=item['filename'],
                    text=text,
                    action_subject=answer.get('action_subject'),
                    timestamp=dt,
                    weight=weight,
                )
                if self.verbose:
                    print('doc:')
                    pprint(doc, indent=4)

                # Register this combination in the database.
                # https://elasticsearch-py.readthedocs.io/en/master/api.html#elasticsearch.Elasticsearch.index
                es.index(
                    id=_id,
                    index=self.kb_index_name,
                    body=doc,
                )

                # Record a hash of this combination so we can skip it next time.
                self.mark_indexed(questions, answer['text'])

        self.last_reindex_count = count
        es.indices.refresh(index=self.kb_index_name)
        self.update_kb_timestamp()
        print(f'\nRe-indexed {count} items.')

    def vprint(self, *args):
        if self.verbose:
            print(' '.join(map(str, args)))
            sys.stdout.flush()

    def get_local_answers(self, q=None):

        def _get_search_results(exact=True):
            # https://www.elastic.co/guide/en/elasticsearch/reference/current/query-dsl-query-string-query.html
            # https://www.elastic.co/guide/en/elasticsearch/reference/current/query-dsl-function-score-query.html#CO158-1
            # Order searches by a mix of how closely they match the query string
            # along with the custom weight.
            es_query = {
                "query": {
                    "function_score": {
                        "boost": '5' if exact else '1',
                        'query': {
                            'query_string':{
                                'query': query,
                                # 'fields': ['questions'],
                                'fields': ['text'],
                                'default_operator': 'AND' if exact else 'OR',
                            },
                        },
                        "functions": [{
                            "script_score": {
                                "script": {
                                    "lang": "painless",
                                    "source": "_score * doc['weight'].value"
                                },
                            },
                        }],
                    }
                }
            }

            if self.verbose:
                print('es_query:')
                pprint(es_query, indent=4)

            results = es.search(index=self.kb_index_name, body=es_query)
            return results

        query = q or self.query
        assert query and isinstance(query, str), f'Invalid query: {query}'
        answers = []
        es = self.get_es()
        self.vprint(f'Checking for local answers at index {self.kb_index_name}...')
        es.indices.create(index=self.kb_index_name, ignore=400)

        # https://elasticsearch-py.readthedocs.io/en/master/api.html#elasticsearch.Elasticsearch.search
        for method in [lambda: _get_search_results(exact=True), lambda: _get_search_results(exact=False)]:
            results = method()
            total = len(results['hits']['hits'])
            self.vprint(f'Found {total} results.')
            hits = results['hits']['hits'][:self.num_answers]
            if self.verbose:
                print('results:')
                pprint(results, indent=4)
            if hits:
                for hit in hits:
                    if self.verbose:
                        print('hit:')
                        pprint(hit, indent=4)
                    answer_data = {}
                    #TODO:sort/boost by weight?
                    #TODO:ignore low weights?
                    score = hit['_score']
                    if self.min_score >= 0 and score < self.min_score:
                        continue

                    answer_data['answer'] = hit['_source']['answer'].strip()
                    answer_data['score'] = score
                    answer_data['source'] = (hit['_source'].get('source') or '').strip() or None
                    _fn = hit['_source']['filename']
                    answer_data['filename'] = _fn
                    answer_data['text'] = hit['_source']['text']
                    answer_data['weight'] = hit['_source']['weight']
                    answer_data['location'] = LOCAL
                    if self.verbose:
                        print('answer_data:')
                        pprint(answer_data, indent=4)
                    answers.append(answer_data)

            # First try finding an entry with all the keywords using the AND operator.
            # If nothing found, then continue by searching for entries with any of the keywords
            # using the OR operator.
            if total:
                break

        return answers

    def reindex(self, *args, **kwargs):
        return self.run_reindex(*args, **kwargs)

    def ask(self, *args, **kwargs):
        return self.run_query(*args, **kwargs)

    def run_query(self, q=None, output=True):
        query = q or self.query

        # Elasticsearch tokenizes text on certain non-alphanumeric characters,
        # so increase a queries chances of finding an exact match by removing these tokens.
        query = re.sub(r'[\:\-]+', ' ', query)

        answers = []
        if query:
            with fasteners.InterProcessLock(self.kb_lockfile_path):

                self.init_kb()
                self.append_header = self.num_answers > 1 or self.show_score or self.show_source
                self.vprint(f'Querying {query}...')

                # Check local index first.
                #http://elasticsearch.org/guide/reference/query-dsl/
                #http://www.elasticsearch.org/guide/en/elasticsearch/reference/current/query-dsl-query-string-query.html
                if not self.ignore_local:
                    answers.extend(self.get_local_answers(query))

                # If we found nothing satisfying locally, then search the net.
                if not answers and not self.ignore_remote:
                    links = self.get_links(query)
                    if not links:
                        return False
                    for answer_number in range(self.num_answers):
                        result = self.get_answer(links)
                        answer, link = result
                        if not answer and not link:
                            continue

                        answer_data = {}
                        answer_data['answer'] = answer
                        answer_data['score'] = 1.0
                        answer_data['source'] = link
                        answer_data['filename'] = None
                        answer_data['text'] = None
                        answer_data['weight'] = 1.0
                        answer_data['location'] = REMOTE
                        answers.append(answer_data)

        if output:
            s = []
            for i, answer in enumerate(answers):
                if answer['location'] == LOCAL:
                    source = answer['filename']
                else:
                    source = answer['source']
                score = int(round(answer['score'] or 0, 0))
                weight = int(answer['weight'] or 0)
                s.append(ANSWER_HEADER.format(
                    i=i + 1,
                    weight=score * weight,
                    answer=answer['answer'],
                    source=source))

            output_str = '\n' + ('\n\n'.join(s)) + '\n'
            output_str.encode('utf-8', 'replace')
            try:
                # Try to print unicode.
                print(output_str)
            except UnicodeEncodeError:
                # If the console forces us to use ASCII, then force ASCII.
                print(output_str.encode('ascii', 'replace'))
            return output_str

        return answers

    def run_reindex(self):
        with fasteners.InterProcessLock(self.kb_lockfile_path):
            self.index_kb()

    def run_summarize_field(self):
        """
        Iterates over all knowledgebase items and counts the values associated with the given field path.
        """
        path = [_.strip() for _ in self.query.split('.') if _.strip()]
        assert path, 'No query path specified.'
        self.vprint('Searching path:', path)
        counts = defaultdict(int)
        for item in self.iter_kb():
            value = get_nested_key(item, path)
            counts[value] += 1
        for v, cnt in sorted(counts.items(), key=lambda o: o[1]):
            print(cnt, v)

    def run_filter_by_field(self):
        """
        Iterates over all knowledgebase items and prints those that correspond to the given field path.
        """
        path, target = self.query.split(' ')
        path = [_.strip() for _ in path.split('.') if _.strip()]
        assert path, 'No query path specified.'
        self.vprint('Searching path:', path)
        self.vprint('Target:', target)
        data = []
        for item in self.iter_kb():
            value = str(get_nested_key(item, path))
            if value != target:
                continue
            if 'answers' in item:
                for answer in item['answers']:
                    answer['text'] = answer['text'].encode('ascii', 'replace')
                    answer['text'] = re.sub(r'\n[\t\s]+\n', '\n\n', answer['text'], flags=re.M)
                    answer['text'] = re.sub(r'(?<=[^\n\t\s])[ ]+(?=$)', '', answer['text'], flags=re.M)
                    answer['text'] = answer['text'].strip() + '\n\n'
            data.append(item)
        yaml.dump(data, stream=sys.stdout, default_flow_style=False, indent=4)

    def run(self):
        run_func = f"run_{self.action.replace('-', '_')}"
        if hasattr(self, run_func):
            return getattr(self, run_func)()
        raise AttributeError(f'Invalid action: {self.action}')


def get_parser():
    parser = argparse.ArgumentParser(description='instant coding answers via the command line')
    parser.add_argument('-v', '--version', help='Show version.', action='version', version=__version__)
    parser.add_argument('--verbose', help='If given, provides excessive output.', default=False, action='store_true')
    parser.add_argument('--kb-filename', help='The knowledge base filename.', default=KNOWLEDGEBASE_FN)
    parser.add_argument('--kb-index-name', help='The knowledge base index name to register in Elasticsearch',
                        default=KNOWLEDGEBASE_INDEX)
    parser.add_argument('--kb-timestamp', help='The filename to use to track timestamps.', default=KNOWLEDGEBASE_TIMESTAMP_FN)
    parser.add_argument('--kb-app-dir', help='The filename to use to tracking timestamps.', default=APP_DATA_DIR)
    parser.add_argument('--kb-lockfile-path', help='The filename to use when locking access during updates.',
                        default=LOCKFILE_PATH)
    parser.add_argument('--cache-dir', help='The filename to use when caching web requests.', default=CACHE_DIR)
    parser.add_argument('--lang', help=f'The localization to use. Default is {LOCALIZATION}.', default=LOCALIZATION)
    parser.add_argument('--action', help=f'Action to perform. One of {"|".join(ACTIONS)}', default=QUERY)
    parser.add_argument('query', metavar='QUERY', type=str, nargs='*',
                        help='The question to answer or keywords to search by. Used with the query action.')
    parser.add_argument('-p', '--pos', help='select answer in specified position (default: 1)', default=1, type=int)
    parser.add_argument('-a', '--all', help='display the full text of the answer', action='store_true')
    parser.add_argument('-l', '--link', help='display only the answer link', action='store_true')
    parser.add_argument('-c', '--color', help='enable colorized output', action='store_true')
    parser.add_argument('-n', '--num-answers', help='number of answers to return', default=1, type=int)
    parser.add_argument('--min-score', help='the minimum score accepted on local answers', default=-1, type=float)
    parser.add_argument('--ignore-local', help='ignore local cache', default=False, action='store_true')
    parser.add_argument('--ignore-remote', help='ignore remote', default=False, action='store_true')
    parser.add_argument('--show-score', help='display score of all results', default=False, action='store_true')
    parser.add_argument('--hide-source', help='displays any source linked to the answer', dest='show_source',
                        default=True, action='store_false')
    parser.add_argument('--force', help='Used with the reindex option, forces reindexing of all items even if no change was made',
                        default=False, action='store_true')

    return parser


def command_line_runner():
    parser = get_parser()
    args = vars(parser.parse_args())
    howdou = HowDoU(**args)
    howdou.run()

if __name__ == '__main__':
    command_line_runner()
