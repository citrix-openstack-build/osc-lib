#   Copyright 2012-2013 OpenStack Foundation
#
#   Licensed under the Apache License, Version 2.0 (the "License"); you may
#   not use this file except in compliance with the License. You may obtain
#   a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#   WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#   License for the specific language governing permissions and limitations
#   under the License.
#

"""Common client utilities"""

import getpass
import logging
import os
import six
import time

from oslo_utils import importutils

from osc_lib import exceptions
from osc_lib.i18n import _


def build_kwargs_dict(arg_name, value):
    """Return a dictionary containing `arg_name` if `value` is set."""
    kwargs = {}
    if value:
        kwargs[arg_name] = value
    return kwargs


def env(*vars, **kwargs):
    """Search for the first defined of possibly many env vars

    Returns the first environment variable defined in vars, or
    returns the default defined in kwargs.
    """
    for v in vars:
        value = os.environ.get(v, None)
        if value:
            return value
    return kwargs.get('default', '')


def find_min_match(items, sort_attr, **kwargs):
    """Find all resources meeting the given minimum constraints

    :param items: A List of objects to consider
    :param sort_attr: Attribute to sort the resulting list
    :param kwargs: A dict of attributes and their minimum values
    :rtype: A list of resources osrted by sort_attr that meet the minimums
    """

    def minimum_pieces_of_flair(item):
        """Find lowest value greater than the minumum"""

        result = True
        for k in kwargs:
            # AND together all of the given attribute results
            result = result and kwargs[k] <= get_field(item, k)
        return result

    return sort_items(filter(minimum_pieces_of_flair, items), sort_attr)


def find_resource(manager, name_or_id, **kwargs):
    """Helper for the _find_* methods.

    :param manager: A client manager class
    :param name_or_id: The resource we are trying to find
    :param kwargs: To be used in calling .find()
    :rtype: The found resource

    This method will attempt to find a resource in a variety of ways.
    Primarily .get() methods will be called with `name_or_id` as an integer
    value, and tried again as a string value.

    If both fail, then a .find() is attempted, which is essentially calling
    a .list() function with a 'name' query parameter that is set to
    `name_or_id`.

    Lastly, if any kwargs are passed in, they will be treated as additional
    query parameters. This is particularly handy in the case of finding
    resources in a domain.

    """

    # Case 1: name_or_id is an ID, we need to call get() directly
    # for example: /projects/454ad1c743e24edcad846d1118837cac
    # For some projects, the name only will work. For keystone, this is not
    # enough information, and domain information is necessary.
    try:
        return manager.get(name_or_id)
    except Exception:
        pass

    # Case 2: name_or_id is a name, but we have query args in kwargs
    # for example: /projects/demo&domain_id=30524568d64447fbb3fa8b7891c10dd6
    try:
        return manager.get(name_or_id, **kwargs)
    except Exception:
        pass

    # Case 3: Try to get entity as integer id. Keystone does not have integer
    # IDs, they are UUIDs, but some things in nova do, like flavors.
    try:
        if isinstance(name_or_id, int) or name_or_id.isdigit():
            return manager.get(int(name_or_id), **kwargs)
    # FIXME(dtroyer): The exception to catch here is dependent on which
    #                 client library the manager passed in belongs to.
    #                 Eventually this should be pulled from a common set
    #                 of client exceptions.
    except Exception as ex:
        if (type(ex).__name__ == 'NotFound' or
                type(ex).__name__ == 'HTTPNotFound'):
            pass
        else:
            raise

    # Case 4: Try to use find.
    # Reset the kwargs here for find
    if len(kwargs) == 0:
        kwargs = {}

    try:
        # Prepare the kwargs for calling find
        if 'NAME_ATTR' in manager.resource_class.__dict__:
            # novaclient does this for oddball resources
            kwargs[manager.resource_class.NAME_ATTR] = name_or_id
        else:
            kwargs['name'] = name_or_id
    except Exception:
        pass

    # finally try to find entity by name
    try:
        return manager.find(**kwargs)
    # FIXME(dtroyer): The exception to catch here is dependent on which
    #                 client library the manager passed in belongs to.
    #                 Eventually this should be pulled from a common set
    #                 of client exceptions.
    except Exception as ex:
        if type(ex).__name__ == 'NotFound':
            msg = _(
                "No %(resource)s with a name or ID of '%(id)s' exists."
            )
            raise exceptions.CommandError(msg % {
                'resource': manager.resource_class.__name__.lower(),
                'id': name_or_id,
            })
        if type(ex).__name__ == 'NoUniqueMatch':
            msg = _(
                "More than one %(resource)s exists with the name '%(id)s'."
            )
            raise exceptions.CommandError(msg % {
                'resource': manager.resource_class.__name__.lower(),
                'id': name_or_id,
            })
        else:
            pass

    # Case 5: For client with no find function, list all resources and hope
    # to find a matching name or ID.
    count = 0
    for resource in manager.list():
        if (resource.get('id') == name_or_id or
                resource.get('name') == name_or_id):
            count += 1
            _resource = resource
    if count == 0:
        # we found no match, report back this error:
        msg = _("Could not find resource %s")
        raise exceptions.CommandError(msg % name_or_id)
    elif count == 1:
        return _resource
    else:
        # we found multiple matches, report back this error
        msg = _("More than one resource exists with the name or ID '%s'.")
        raise exceptions.CommandError(msg % name_or_id)


def format_dict(data):
    """Return a formatted string of key value pairs

    :param data: a dict
    :rtype: a string formatted to key='value'
    """

    output = ""
    for s in sorted(data):
        output = output + s + "='" + six.text_type(data[s]) + "', "
    return output[:-2]


def format_list(data, separator=', '):
    """Return a formatted strings

    :param data: a list of strings
    :param separator: the separator to use between strings (default: ', ')
    :rtype: a string formatted based on separator
    """

    return separator.join(sorted(data))


def format_list_of_dicts(data):
    """Return a formatted string of key value pairs for each dict

    :param data: a list of dicts
    :rtype: a string formatted to key='value' with dicts separated by new line
    """

    return '\n'.join(format_dict(i) for i in data)


def get_client_class(api_name, version, version_map):
    """Returns the client class for the requested API version

    :param api_name: the name of the API, e.g. 'compute', 'image', etc
    :param version: the requested API version
    :param version_map: a dict of client classes keyed by version
    :rtype: a client class for the requested API version
    """
    try:
        client_path = version_map[str(version)]
    except (KeyError, ValueError):
        msg = _(
            "Invalid %(api_name)s client version '%(version)s'. "
            "must be one of: %(version_map)s"
        )
        raise exceptions.UnsupportedVersion(msg % {
            'api_name': api_name,
            'version': version,
            'version_map': ', '.join(list(version_map.keys())),
        })

    return importutils.import_class(client_path)


def get_dict_properties(item, fields, mixed_case_fields=None, formatters=None):
    """Return a tuple containing the item properties.

    :param item: a single dict resource
    :param fields: tuple of strings with the desired field names
    :param mixed_case_fields: tuple of field names to preserve case
    :param formatters: dictionary mapping field names to callables
       to format the values
    """
    if mixed_case_fields is None:
        mixed_case_fields = []
    if formatters is None:
        formatters = {}

    row = []

    for field in fields:
        if field in mixed_case_fields:
            field_name = field.replace(' ', '_')
        else:
            field_name = field.lower().replace(' ', '_')
        data = item[field_name] if field_name in item else ''
        if field in formatters:
            row.append(formatters[field](data))
        else:
            row.append(data)
    return tuple(row)


def get_effective_log_level():
    """Returns the lowest logging level considered by logging handlers

    Retrieve and return the smallest log level set among the root
    logger's handlers (in case of multiple handlers).
    """
    root_log = logging.getLogger()
    min_log_lvl = logging.CRITICAL
    for handler in root_log.handlers:
        min_log_lvl = min(min_log_lvl, handler.level)
    return min_log_lvl


def get_field(item, field):
    try:
        if isinstance(item, dict):
            return item[field]
        else:
            return getattr(item, field)
    except Exception:
        msg = _("Resource doesn't have field %s")
        raise exceptions.CommandError(msg % field)


def get_item_properties(item, fields, mixed_case_fields=None, formatters=None):
    """Return a tuple containing the item properties.

    :param item: a single item resource (e.g. Server, Project, etc)
    :param fields: tuple of strings with the desired field names
    :param mixed_case_fields: tuple of field names to preserve case
    :param formatters: dictionary mapping field names to callables
       to format the values
    """
    if mixed_case_fields is None:
        mixed_case_fields = []
    if formatters is None:
        formatters = {}

    row = []

    for field in fields:
        if field in mixed_case_fields:
            field_name = field.replace(' ', '_')
        else:
            field_name = field.lower().replace(' ', '_')
        data = getattr(item, field_name, '')
        if field in formatters:
            row.append(formatters[field](data))
        else:
            row.append(data)
    return tuple(row)


def get_password(stdin, prompt=None, confirm=True):
    message = prompt or "User Password:"
    if hasattr(stdin, 'isatty') and stdin.isatty():
        try:
            while True:
                first_pass = getpass.getpass(message)
                if not confirm:
                    return first_pass
                second_pass = getpass.getpass("Repeat " + message)
                if first_pass == second_pass:
                    return first_pass
                msg = _("The passwords entered were not the same")
                print(msg)
        except EOFError:  # Ctl-D
            msg = _("Error reading password")
            raise exceptions.CommandError(msg)
    msg = _("No terminal detected attempting to read password")
    raise exceptions.CommandError(msg)


def is_ascii(string):
    try:
        (string.decode('ascii') if isinstance(string, bytes)
            else string.encode('ascii'))
        return True
    except (UnicodeEncodeError, UnicodeDecodeError):
        return False


def read_blob_file_contents(blob_file):
    try:
        with open(blob_file) as file:
            blob = file.read().strip()
        return blob
    except IOError:
        msg = _("Error occurred trying to read from file %s")
        raise exceptions.CommandError(msg % blob_file)


def sort_items(items, sort_str):
    """Sort items based on sort keys and sort directions given by sort_str.

    :param items: a list or generator object of items
    :param sort_str: a string defining the sort rules, the format is
        '<key1>:[direction1],<key2>:[direction2]...', direction can be 'asc'
        for ascending or 'desc' for descending, if direction is not given,
        it's ascending by default
    :return: sorted items
    """
    if not sort_str:
        return items
    # items may be a generator object, transform it to a list
    items = list(items)
    sort_keys = sort_str.strip().split(',')
    for sort_key in reversed(sort_keys):
        reverse = False
        if ':' in sort_key:
            sort_key, direction = sort_key.split(':', 1)
            if not sort_key:
                msg = _("'<empty string>'' is not a valid sort key")
                raise exceptions.CommandError(msg)
            if direction not in ['asc', 'desc']:
                if not direction:
                    direction = "<empty string>"
                msg = _(
                    "'%(direction)s' is not a valid sort direction for "
                    "sort key %(sort_key)s, use 'asc' or 'desc' instead"
                )
                raise exceptions.CommandError(msg % {
                    'direction': direction,
                    'sort_key': sort_key,
                })
            if direction == 'desc':
                reverse = True
        items.sort(key=lambda item: get_field(item, sort_key),
                   reverse=reverse)
    return items


def wait_for_delete(manager,
                    res_id,
                    status_field='status',
                    error_status=['error'],
                    exception_name=['NotFound'],
                    sleep_time=5,
                    timeout=300,
                    callback=None):
    """Wait for resource deletion

    :param manager: the manager from which we can get the resource
    :param res_id: the resource id to watch
    :param status_field: the status attribute in the returned resource object,
        this is used to check for error states while the resource is being
        deleted
    :param error_status: a list of status strings for error
    :param exception_name: a list of exception strings for deleted case
    :param sleep_time: wait this long between checks (seconds)
    :param timeout: check until this long (seconds)
    :param callback: called per sleep cycle, useful to display progress; this
        function is passed a progress value during each iteration of the wait
        loop
    :rtype: True on success, False if the resource has gone to error state or
        the timeout has been reached
    """
    total_time = 0
    while total_time < timeout:
        try:
            # might not be a bad idea to re-use find_resource here if it was
            # a bit more friendly in the exceptions it raised so we could just
            # handle a NotFound exception here without parsing the message
            res = manager.get(res_id)
        except Exception as ex:
            if type(ex).__name__ in exception_name:
                return True
            raise

        status = getattr(res, status_field, '').lower()
        if status in error_status:
            return False

        if callback:
            progress = getattr(res, 'progress', None) or 0
            callback(progress)
        time.sleep(sleep_time)
        total_time += sleep_time

    # if we got this far we've timed out
    return False


def wait_for_status(status_f,
                    res_id,
                    status_field='status',
                    success_status=['active'],
                    error_status=['error'],
                    sleep_time=5,
                    callback=None):
    """Wait for status change on a resource during a long-running operation

    :param status_f: a status function that takes a single id argument
    :param res_id: the resource id to watch
    :param status_field: the status attribute in the returned resource object
    :param success_status: a list of status strings for successful completion
    :param error_status: a list of status strings for error
    :param sleep_time: wait this long (seconds)
    :param callback: called per sleep cycle, useful to display progress
    :rtype: True on success
    """
    while True:
        res = status_f(res_id)
        status = getattr(res, status_field, '').lower()
        if status in success_status:
            retval = True
            break
        elif status in error_status:
            retval = False
            break
        if callback:
            progress = getattr(res, 'progress', None) or 0
            callback(progress)
        time.sleep(sleep_time)
    return retval
