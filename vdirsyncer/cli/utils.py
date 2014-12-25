# -*- coding: utf-8 -*-
'''
    vdirsyncer.cli.utils
    ~~~~~~~~~~~~~~~~~~~~

    :copyright: (c) 2014 Markus Unterwaditzer & contributors
    :license: MIT, see LICENSE for more details.
'''

import errno
import hashlib
import json
import os
import string
import sys
import threading
from itertools import chain

from .. import DOCS_HOME, PROJECT_HOME, log
from ..doubleclick import click
from ..storage import storage_names
from ..sync import StorageEmpty, SyncConflict
from ..utils import expand_path, get_class_init_args, parse_options, \
    safe_write


try:
    from ConfigParser import RawConfigParser
except ImportError:
    from configparser import RawConfigParser

try:
    from Queue import Queue
except ImportError:
    from queue import Queue


cli_logger = log.get(__name__)

GENERAL_ALL = frozenset(['status_path', 'password_command'])
GENERAL_REQUIRED = frozenset(['status_path'])
SECTION_NAME_CHARS = frozenset(chain(string.ascii_letters, string.digits, '_'))


class CliError(RuntimeError):
    pass


class JobFailed(RuntimeError):
    pass


def handle_cli_error(status_name='sync'):
    try:
        raise
    except CliError as e:
        cli_logger.critical(str(e))
    except StorageEmpty as e:
        cli_logger.error(
            '{status_name}: Storage "{name}" was completely emptied. Use '
            '`vdirsyncer sync --force-delete {status_name}` to synchronize '
            'that emptyness to the other side, or delete the status by '
            'yourself to restore the items from the non-empty side.'.format(
                name=e.empty_storage.instance_name,
                status_name=status_name
            )
        )
    except SyncConflict as e:
        cli_logger.error(
            '{status_name}: One item changed on both sides. Resolve this '
            'conflict manually, or by setting the `conflict_resolution` '
            'parameter in your config file.\n'
            'See also {docs}/api.html#pair-section\n'
            'Item ID: {e.ident}\n'
            'Item href on side A: {e.href_a}\n'
            'Item href on side B: {e.href_b}\n'
            .format(status_name=status_name, e=e, docs=DOCS_HOME)
        )
    except (click.Abort, KeyboardInterrupt, JobFailed):
        pass
    except Exception as e:
        cli_logger.exception('Unhandled exception occured while syncing {}.'
                             .format(status_name))
    else:
        return True


def validate_section_name(name, section_type):
    invalid = set(name) - SECTION_NAME_CHARS
    if invalid:
        chars_display = ''.join(sorted(SECTION_NAME_CHARS))
        raise CliError('The {}-section {!r} contains invalid characters. Only '
                       'the following characters are allowed for storage and '
                       'pair names:\n{}'.format(section_type, name,
                                                chars_display))


def _parse_old_config_list_value(d, key):
    value = d.get(key, [])
    if isinstance(value, str):
        # XXX: Deprecation
        old_form = value
        value = list(filter(bool, (x.strip() for x in value.split(','))))
        cli_logger.warning(
            '{!r} is deprecated, please use:\n{} = {}\n'
            'The old form will be removed in 0.4.0.'
            .format(old_form, key, json.dumps(value)))
    return value


def get_status_name(pair, collection):
    if collection is None:
        return pair
    return pair + '/' + collection


def _get_collections_cache_key(pair_options, config_a, config_b):
    m = hashlib.sha256()
    j = json.dumps([pair_options, config_a, config_b], sort_keys=True)
    m.update(j.encode('utf-8'))
    return m.hexdigest()


def collections_for_pair(status_path, name_a, name_b, pair_name, config_a,
                         config_b, pair_options, skip_cache=False):
    '''Determine all configured collections for a given pair. Takes care of
    shortcut expansion and result caching.

    :param status_path: The path to the status directory.
    :param name_a: The config name of storage A.
    :param name_b: The config name of storage B.
    :param pair_name: The config name of the pair.
    :param config_a: The configuration for storage A.
    :param config_b: The configuration for storage B.
    :param pair_options: Pair-specific options.
    :param skip_cache: Whether to skip the cached data and always do discovery.
        Even with this option enabled, the new cache is written.

    :returns: iterable of (collection, a_args, b_args)
    '''
    rv = load_status(status_path, pair_name, data_type='collections')
    cache_key = _get_collections_cache_key(pair_options, config_a, config_b)
    if rv and not skip_cache:
        if rv.get('cache_key', None) == cache_key:
            return rv.get('collections', rv)
        elif rv:
            cli_logger.info('Detected change in config file, discovering '
                            'collections for {}'.format(pair_name))

    cli_logger.info('Discovering collections for pair {}'
                    .format(pair_name))

    # We have to use a list here because the special None/null value would get
    # mangled to string (because JSON objects always have string keys).
    rv = list(_collections_for_pair_impl(status_path, name_a, name_b,
                                         pair_name, config_a, config_b,
                                         pair_options))
    save_status(status_path, pair_name, data_type='collections',
                data={'collections': rv, 'cache_key': cache_key})
    return rv


def _discover_from_config(config):
    storage_type = config['type']
    cls, config = storage_class_from_config(config)

    try:
        discovered = list(cls.discover(**config))
    except Exception:
        return handle_storage_init_error(cls, config)
    else:
        rv = {}
        for args in discovered:
            args['type'] = storage_type
            rv[args['collection']] = args
        return rv


def _get_coll(pair_name, storage_name, collection, discovered, config):
    try:
        return discovered[collection]
    except KeyError:
        storage_type = config['type']
        cls, config = storage_class_from_config(config)
        try:
            args = cls.join_collection(collection=collection, **config)
            args['type'] = storage_type
            return args
        except NotImplementedError:
            raise CliError(
                '{pair}: Unable to find collection {collection!r} for storage '
                '{storage!r}. A same-named collection was found for the other '
                'storage, and vdirsyncer is configured to synchronize '
                'those.'.format(pair=pair_name, collection=collection,
                                storage=storage_name))


def _collections_for_pair_impl(status_path, name_a, name_b, pair_name,
                               config_a, config_b, pair_options):

    shortcuts = set(_parse_old_config_list_value(pair_options, 'collections'))
    if not shortcuts:
        yield None, (config_a, config_b)
    else:
        a_discovered = _discover_from_config(config_a)
        b_discovered = _discover_from_config(config_b)

        for shortcut in shortcuts:
            if shortcut == 'from a':
                collections = a_discovered
            elif shortcut == 'from b':
                collections = b_discovered
            else:
                collections = [shortcut]

            for collection in collections:
                a_args = _get_coll(pair_name, name_a, collection, a_discovered,
                                   config_a)
                b_args = _get_coll(pair_name, name_b, collection, b_discovered,
                                   config_b)
                yield collection, (a_args, b_args)


def _validate_general_section(general_config):
    if general_config is None:
        raise CliError(
            'Unable to find general section. You should copy the example '
            'config from the repository and edit it.\n{}'.format(PROJECT_HOME)
        )

    if 'passwordeval' in general_config:
        # XXX: Deprecation
        cli_logger.warning('The `passwordeval` parameter has been renamed to '
                           '`password_command`.')

    invalid = set(general_config) - GENERAL_ALL
    missing = GENERAL_REQUIRED - set(general_config)

    if invalid:
        cli_logger.critical(u'general section doesn\'t take the parameters: {}'
                            .format(u', '.join(invalid)))

    if missing:
        cli_logger.critical(u'general section is missing the parameters: {}'
                            .format(u', '.join(missing)))

    if invalid or missing:
        raise CliError('Invalid general section.')


def load_config(f):
    c = RawConfigParser()
    c.readfp(f)

    get_options = lambda s: dict(parse_options(c.items(s), section=s))

    general = None
    pairs = {}
    storages = {}

    def handle_storage(storage_name, options):
        validate_section_name(storage_name, 'storage')
        storages.setdefault(storage_name, {}).update(options)
        storages[storage_name]['instance_name'] = storage_name

    def handle_pair(pair_name, options):
        validate_section_name(pair_name, 'pair')
        a, b = options.pop('a'), options.pop('b')
        pairs[pair_name] = a, b, options

    def bad_section(name, options):
        cli_logger.error('Unknown section: {}'.format(name))

    handlers = {'storage': handle_storage, 'pair': handle_pair}

    for section in c.sections():
        if ' ' in section:
            section_type, name = section.split(' ', 1)
        else:
            section_type = name = section

        if section_type == 'general':
            if general is not None:
                raise CliError('More than one general section in config file.')
            general = get_options(section_type)
        else:
            handlers.get(section_type, bad_section)(name, get_options(section))

    _validate_general_section(general)
    return general, pairs, storages


def load_status(base_path, pair, collection=None, data_type=None):
    assert data_type is not None
    status_name = get_status_name(pair, collection)
    path = expand_path(os.path.join(base_path, status_name))
    if os.path.isfile(path) and data_type == 'items':
        new_path = path + '.items'
        cli_logger.warning('Migrating statuses: Renaming {} to {}'
                           .format(path, new_path))
        os.rename(path, new_path)

    path += '.' + data_type
    if not os.path.exists(path):
        return None

    with open(path) as f:
        try:
            return dict(json.load(f))
        except ValueError:
            pass

        f.seek(0)
        try:
            return dict(json.loads(line) for line in f)
        except ValueError:
            pass

    return {}


def save_status(base_path, pair, collection=None, data_type=None, data=None):
    assert data_type is not None
    assert data is not None
    status_name = get_status_name(pair, collection)
    path = expand_path(os.path.join(base_path, status_name)) + '.' + data_type
    base_path = os.path.dirname(path)

    if collection is not None and os.path.isfile(base_path):
        raise CliError('{} is probably a legacy file and could be removed '
                       'automatically, but this choice is left to the '
                       'user. If you think this is an error, please file '
                       'a bug at {}'.format(base_path, PROJECT_HOME))

    try:
        os.makedirs(base_path, 0o750)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise

    with safe_write(path, 'w+') as f:
        json.dump(data, f)


def storage_class_from_config(config):
    config = dict(config)
    storage_name = config.pop('type')
    cls = storage_names.get(storage_name, None)
    if cls is None:
        raise CliError('Unknown storage type: {}'.format(storage_name))
    return cls, config


def storage_instance_from_config(config):
    '''
    :param config: A configuration dictionary to pass as kwargs to the class
        corresponding to config['type']
    :param description: A name for the storage for debugging purposes
    '''

    cls, config = storage_class_from_config(config)

    try:
        return cls(**config)
    except Exception:
        handle_storage_init_error(cls, config)


def handle_storage_init_error(cls, config):
    e = sys.exc_info()[1]
    if isinstance(e, (click.Abort, CliError, KeyboardInterrupt)):
        raise

    all, required = get_class_init_args(cls)
    given = set(config)
    missing = required - given
    invalid = given - all

    if missing:
        cli_logger.critical(
            u'{} storage requires the parameters: {}'
            .format(cls.storage_name, u', '.join(missing)))

    if invalid:
        cli_logger.critical(
            u'{} storage doesn\'t take the parameters: {}'
            .format(cls.storage_name, u', '.join(invalid)))

    if not missing and not invalid:
        cli_logger.exception('')

    raise CliError('Failed to initialize {}.'.format(config['instance_name']))


def parse_pairs_args(pairs_args, all_pairs):
    '''
    Expand the various CLI shortforms ("pair, pair/collection") to an iterable
    of (pair, collections).
    '''
    rv = {}
    for pair_and_collection in (pairs_args or all_pairs):
        pair, collection = pair_and_collection, None
        if '/' in pair:
            pair, collection = pair.split('/')

        if pair not in all_pairs:
            raise CliError('Pair not found: {}\n'
                           'These are the pairs found: {}'
                           .format(pair, list(all_pairs)))

        collections = rv.setdefault(pair, set())
        if collection:
            collections.add(collection)

    return rv.items()


class WorkerQueue(object):
    def __init__(self, max_workers):
        self._queue = Queue()
        self._workers = []
        self._exceptions = []
        self._max_workers = max_workers

    def _process_job(self):
        func = self._queue.get()
        try:
            func(wq=self)
        except:
            if not handle_cli_error():
                self._exceptions.append(sys.exc_info()[1])
        finally:
            self._queue.task_done()

    def spawn_worker(self):
        if self._max_workers and len(self._workers) >= self._max_workers:
            return

        def worker():
            while True:
                self._process_job()

        t = threading.Thread(target=worker)
        t.daemon = True
        t.start()
        self._workers.append(t)

    def join(self):
        self._queue.join()
        if self._exceptions:
            sys.exit(1)

    def put(self, f):
        return self._queue.put(f)
