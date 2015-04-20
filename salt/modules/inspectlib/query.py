# -*- coding: utf-8 -*-
#
# Copyright 2014 SUSE LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


import os
import logging

import salt
import salt.utils.network
from salt.modules.inspectlib.dbhandle import DBHandle
from salt.modules.inspectlib.exceptions import (InspectorQueryException, SIException)

log = logging.getLogger(__name__)


class SysInfo(object):
    '''
    System information.
    '''

    def __init__(self, systype):
        if systype.lower() == "solaris":
            raise SIException("Platform {0} not (yet) supported.".format(systype))

    def _grain(self, grain):
        '''
        An alias for grains getter.
        '''
        return __grains__.get(grain, 'N/A')

    def _get_disk_size(self, device):
        '''
        Get a size of a disk.
        '''
        out = __salt__['cmd.run_all']("df {0}".format(device))
        if out['retcode']:
            msg = "Disk size info error: {0}".format(out['stderr'])
            log.error(msg)
            raise SIException(msg)

        devpath, blocks, used, available, used_p, mountpoint = [elm for elm in out['stdout'].split(os.linesep)[-1].split(" ") if elm]

        return {
            'device': devpath, 'blocks': blocks, 'used': used,
            'available': available, 'used (%)': used_p, 'mounted': mountpoint,
        }

    def _get_fs(self):
        '''
        Get available file systems and their types.
        '''

        out = __salt__['cmd.run_all']("blkid -o export")
        salt.utils.fsutils._verify_run(out)

        data = dict()
        for dev, dev_data in salt.utils.fsutils._blkid_output(out['stdout']).items():
            dev = self._get_disk_size(dev)
            device = dev.pop('device')
            dev['type'] = dev_data['type']
            data[device] = dev

        return data

    def _get_mounts(self):
        '''
        Get mounted FS on the system.
        '''
        return salt.utils.fsutils._get_mounts()

    def _get_cpu(self):
        '''
        Get available CPU information.
        '''
        # CPU data in grains is OK-ish, but lscpu is still better in this case
        out = __salt__['cmd.run_all']("lscpu")
        salt.utils.fsutils._verify_run(out)
        data = dict()
        for descr, value in [elm.split(":", 1) for elm in out['stdout'].split(os.linesep)]:
            data[descr.strip()] = value.strip()

        return data

    def _get_mem(self):
        '''
        Get memory.
        '''
        out = __salt__['cmd.run_all']("vmstat -s")
        if out['retcode']:
            raise SIException("Memory info error: {0}".format(out['stderr']))

        ret = dict()
        for line in out['stdout'].split(os.linesep):
            line = line.strip()
            if not line:
                continue
            size, descr = line.split(" ", 1)
            if descr.startswith("K "):
                descr = descr[2:]
                size = size + "K"
            ret[descr] = size
        return ret

    def _get_network(self):
        '''
        Get network configuration.
        '''
        data = dict()
        data['interfaces'] = salt.utils.network.interfaces()
        data['subnets'] = salt.utils.network.subnets()

        return data

    def _get_os(self):
        '''
        Get operating system summary
        '''
        return {
            'name': self._grain('os'),
            'family': self._grain('os_family'),
            'arch': self._grain('osarch'),
            'release': self._grain('osrelease'),
        }



class Query(object):
    '''
    Query the system.
    This class is actually puts all Salt features together,
    so there would be no need to pick it from various places.
    '''

    # Configuration: config files
    # Identity: users/groups
    # Software: packages, patterns, repositories
    # Services
    # System: distro, RAM etc
    # Changes: all files that are managed and were changed from the original
    # all: include all scopes (scary!)
    # payload: files that are not managed

    SCOPES = ["changes", "configuration", "identity", "system", "software", "services", "payload", "all"]

    def __init__(self, scope):
        '''
        Constructor.

        :param scope:
        :return:
        '''
        if scope not in self.SCOPES:
            raise InspectorQueryException(
                "Unknown scope: {0}. Must be one of: {1}".format(repr(scope), ", ".join(self.SCOPES)))
        self.scope = '_' + scope
        self.db = DBHandle(globals()['__salt__']['config.get']('inspector.db', ''))
        self.local_identity = dict()

    def __call__(self, *args, **kwargs):
        '''
        Call the query with the defined scope.

        :param args:
        :param kwargs:
        :return:
        '''

        return getattr(self, self.scope)(*args, **kwargs)

    def _changes(self, *args, **kwargs):
        '''
        Returns all diffs to the configuration files.
        '''
        raise Exception("Not yet implemented")

    def _configuration(self, *args, **kwargs):
        '''
        Return configuration files.
        '''

        data = dict()
        self.db.open()
        self.db.cursor.execute("SELECT id, name FROM inspector_pkg")
        for pkg_id, pkg_name in self.db.cursor.fetchall():
            self.db.cursor.execute("SELECT id, path FROM inspector_pkg_cfg_files WHERE pkgid=?", (pkg_id,))
            configs = list()
            for cnf_id, cnf_name in self.db.cursor.fetchall():
                configs.append(cnf_name)
            data[pkg_name] = configs
        self.db.close()

        if not data:
            raise InspectorQueryException("No inspected configuration yet available.")

        return data

    def _get_local_users(self, disabled=None):
        '''
        Return all known local accounts to the system.
        '''
        users = dict()
        for line in open("/etc/passwd").xreadlines():
            line = line.strip()
            if ":" not in line:
                continue
            name, password, uid, gid, gecos, directory, shell = line.split(":")
            active = not (password == "*" or password.startswith("!"))
            if (disabled is False and active) or (disabled is True and not active) or disabled is None:
                users[name] = {
                    'uid': uid,
                    'git': gid,
                    'info': gecos,
                    'home': directory,
                    'shell': shell,
                    'disabled': not active
                }

        return users

    def _get_local_groups(self):
        '''
        Return all known local groups to the system.
        '''
        groups = dict()
        for line in open("/etc/group").xreadlines():
            line = line.strip()
            if ":" not in line:
                continue
            name, password, gid, users = line.split(":")
            groups[name] = {
                'gid': gid,
            }

            if users:
                groups[name]['users'] = users.split(',')

        return groups


    def _get_external_accounts(self, locals):
        '''
        Return all known accounts, excluding local accounts.
        '''
        users = dict()
        out = __salt__['cmd.run_all']("passwd -S -a")
        if out['retcode']:
            # System does not supports all accounts descriptions, just skipping.
            return users
        status = {'L': 'Locked', 'NP': 'No password', 'P': 'Usable password', 'LK': 'Locked'}
        for data in [elm.strip().split(" ") for elm in out['stdout'].split(os.linesep) if elm.strip()]:
            if len(data) < 2:
                continue
            name, login = data[:2]
            if name not in locals:
                users[name] = {
                    'login': login,
                    'status': status.get(login, 'N/A')
                }

        return users

    def _identity(self, *args, **kwargs):
        '''
        Local users and groups.

        accounts
            Can be either 'local', 'remote' or 'all' (equal to "local,remote").
            Remote accounts cannot be resolved on all systems, but only
            those, which supports 'passwd -S -a'.

        disabled
            True (or False, default) to return only disabled accounts.
        '''
        LOCAL = 'local accounts'
        EXT = 'external accounts'

        data = dict()
        data[LOCAL] = self._get_local_users(disabled=kwargs.get('disabled'))
        data[EXT] = self._get_external_accounts(data[LOCAL].keys()) or 'N/A'
        data['local groups'] = self._get_local_groups()

        return data

    def _system(self, *args, **kwargs):
        '''
        This basically calls grains items and picks out only
        necessary information in a certain structure.

        :param args:
        :param kwargs:
        :return:
        '''
        sysinfo = SysInfo(__grains__.get("kernel"))

        data = dict()
        data['cpu'] = sysinfo._get_cpu()
        data['disks'] = sysinfo._get_fs()
        data['mounts'] = sysinfo._get_mounts()
        data['memory'] = sysinfo._get_mem()
        data['network'] = sysinfo._get_network()
        data['os'] = sysinfo._get_os()

        return data

    def _software(self, *args, **kwargs):
        '''
        Return installed software.
        '''
        data = dict()
        if 'exclude' in kwargs:
            excludes = kwargs['exclude'].split(",")
        else:
            excludes = list()

        os_family = __grains__.get("os_family").lower()

        # Get locks
        if os_family == 'suse':
            LOCKS = "pkg.list_locks"
            if 'products' not in excludes:
                products = __salt__['pkg.list_products']()
                if products:
                    data['products'] = products
        elif os_family == 'redhat':
            LOCKS = "pkg.get_locked_packages"
        else:
            LOCKS = None

        if LOCKS and 'locks' not in excludes:
            locks = __salt__[LOCKS]()
            if locks:
                data['locks'] = locks

        # Get patterns
        if os_family == 'suse':
            PATTERNS = 'pkg.list_installed_patterns'
        elif os_family == 'redhat':
            PATTERNS = 'pkg.group_list'
        else:
            PATTERNS = None

        if PATTERNS and 'patterns' not in excludes:
            patterns = __salt__[PATTERNS]()
            if patterns:
                data['patterns'] = patterns

        # Get packages
        if 'packages' not in excludes:
            data['packages'] = __salt__['pkg.list_pkgs']()

        # Get repositories
        if 'repositories' not in excludes:
            repos = __salt__['pkg.list_repos']()
            if repos:
                data['repositories'] = repos

        return data

    def _services(self, *args, **kwargs):
        '''
        Get list of enabled and disabled services on the particular system.
        '''
        return {
            'enabled': __salt__['service.get_enabled'](),
            'disabled': __salt__['service.get_disabled'](),
        }

    def _id_resolv(self, iid, named=True, uid=True):
        '''
        Resolve local users and groups.

        :param iid:
        :param named:
        :param uid:
        :return:
        '''

        if not self.local_identity:
            self.local_identity['users'] = self._get_local_users()
            self.local_identity['groups'] = self._get_local_groups()

        if not named:
            return iid

        for name, meta in self.local_identity[uid and 'users' or 'groups'].items():
            if (uid and int(meta.get('uid', -1)) == iid) or (not uid and int(meta.get('gid', -1)) == iid):
                return name

        return iid

    def _payload(self, *args, **kwargs):
        '''
        Find all unmanaged files.
        '''
        data = dict()
        return data

    def _all(self, *args, **kwargs):
        '''
        Return all the summary of the particular system.
        '''
        data = dict()
        data['software'] = self._software(**kwargs)
        data['system'] = self._system(**kwargs)
        data['services'] = self._services(**kwargs)
        try:
            data['configuration'] = self._configuration(**kwargs)
        except InspectorQueryException as ex:
            data['configuration'] = 'N/A'
            log.error(ex)
        data['payload'] = self._payload(**kwargs) or 'N/A'

        return data

