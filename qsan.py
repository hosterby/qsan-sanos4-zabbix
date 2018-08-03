# encoding: utf8
import argparse
import sys
import requests
import json
from requests.exceptions import (ConnectionError, ConnectTimeout, Timeout,
                                 ChunkedEncodingError, ReadTimeout, HTTPError,
                                 TooManyRedirects, InvalidHeader, RetryError,
                                 ContentDecodingError, StreamConsumedError,
                                 UnrewindableBodyError, RequestException,
                                 ProxyError, SSLError, MissingSchema)
from bs4 import BeautifulSoup


def argumentsparsing():
    """
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--method", required=True, type=str, dest="method",
                        help="Available methods: discovery, stat:ctrl")
    parser.add_argument("--host", dest="host", required=True, type=str,
                        help="QSAN IP-address or FQDN")
    parser.add_argument("--username", type=str, dest="username",
                        default="user",
                        help="QSAN readonly username [default: %(default)s]")
    parser.add_argument("--password", type=str, dest="password",
                        default="1234",
                        help="QSAN user password [default: %(default)s]")
    parser.add_argument("--zhost", type=str, dest="zhost",
                        help="Storage name in Zabbix")

    return parser.parse_args()


class QSAN():
    """
    Class for operationg with qsan
    """

    # Common header for HTTP request
    _HEADERS = {
        'Host': None,
        'User-Agent': sys.argv[0] +
        ', Python ' + sys.version.replace('\n', ''),
        'Connection': 'keep-alive',
        'Accept': ('text/html,application/xhtml+xml,application/xml'),
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate, br',
        'Content-Type': 'application/x-www-form-urlencoded'
    }

    # Default login params
    _LOGIN_KEYS = {
        'lang_sel': 'en',
        'login': 'Login'
    }

    def __init__(self, host=None, username='user', password='1234'):
        """
        Connecting to QSAN storage. Makinkg discovery of Volumes and Disks.
        """
        self._connection_timeout = 30
        self._session = None
        self._data = None
        self._soup = None
        self._url = 'http://' + host
        self._url_path_login = '/login.php'
        self._url_path_data = '/monitor_x.php?cmd=monitor_dashboard'
        self._url_path_VD = '/vd_x.php?size_unit=gb'
        self._url_path_select_stats_VD = '/monitor_x.php?op=volume_set_monitor'
        self._url_path_VD_stats = '/monitor_x.php?cmd=monitor_volume'
        self._url_path_DISK = '/pd_x.php?enc_idx=0&pd_size_unit=gb'
        self._url_path_select_stats_DISK = ('/monitor_x.php' +
                                            '?op=disk_set_monitor&enc_idx=0')
        self._url_path_DISK_stats = '/monitor_x.php?cmd=monitor_disk'
        self._username = username
        self._password = password
        self._VDs = {}
        self._DISKs = {}
        self.connect()
        self.vd_discovery()
        self.disk_discovery()

    def _bs4(self, r):
        """
        Common method for BeautifulSoup
        Returns: BeautifulSoup object
        """
        return BeautifulSoup(r.text, 'lxml')

    def _is_request_ok(self, r):
        """
        Checking HTTP respose status
        Returns: bool
        """

        return (r.ok or r.status_code == 200 or r.status_code == 302)

    def _connection_init(self):
        """
        Closing session and establishing new one
        """
        if self._session:
            self._session.close()

        self._session = requests.Session()

    def _connection(self, url, username, password, post=False, data=None):
        """
        Main connection method
        Returns: session object if got succesfull HTTP response
        with _is_request_ok()
        """
        if username:
            self._LOGIN_KEYS['username'] = username
        if password:
            self._LOGIN_KEYS['password'] = password

        try:
            if post:
                r = self._session.post(url,
                                       headers=self._HEADERS,
                                       timeout=self._connection_timeout,
                                       data=data)
            else:
                r = self._session.get(url,
                                      headers=self._HEADERS,
                                      timeout=self._connection_timeout,
                                      data=data)

            self._soup = self._bs4(r)

            if not self._is_request_ok(r):
                raise RequestException('Something wrong with request')

            return r
        except (RequestException or ConnectionError or ConnectTimeout or
                SSLError or MissingSchema or RetryError or ProxyError or
                InvalidHeader or ReadTimeout or UnrewindableBodyError or
                ChunkedEncodingError or HTTPError or StreamConsumedError or
                Timeout or TooManyRedirects or ContentDecodingError) as e:

            raise RequestException('Error making request: ' + str(e))

    def _is_authorized(self, r):
        """
        Checking if LOGOUT div present on a page
        """
        res = self._soup.find('div', id='logout_btn')

        # F600Q Support (SANOS3?)
        if not res:
            res = self._soup.find('img', title='Logout')

        if res:
            return True

        return False

    def _authorize(self):
        """
        Authorizing at QSAN management web-interface
        Returns: bool with _is_authorized()
        """
        r = self._connection(self._url + self._url_path_login,
                             username=self._username,
                             password=self._password,
                             post=True,
                             data=self._LOGIN_KEYS)

        return self._is_authorized(r)

    def connect(self):
        """
        Common connect method
        """
        self._connection_init()

        if not self._authorize():
            raise RequestException('Unable to authorize!')

    def storage_stats(self):
        """
        Getting stats from dashboard
        Returns: {'iops': '10764', 'read': '282640625', 'write': '1255703125'}
        """
        stats = {
            'iops': None,
            'tx': None,
            'rx': None
        }

        self._connection(self._url + self._url_path_data,
                         username=None,
                         password=None,
                         data=None)

        # SANOS3-based storages doesn't support storage stats
        if not self._soup.response.controller:
            return {}

        for s in stats:
            value = self._soup.response.find(s).text.replace(',', '')
            stats[s] = value

        stats['read'] = stats.pop('tx')
        stats['write'] = stats.pop('rx')

        stats['iops'] = stats['iops']
        # Converting to Bps
        stats['read'] = str(int(float(stats['read']) * 1048576))
        stats['write'] = str(int(float(stats['write']) * 1048576))

        return stats

    def _get_VD_name_by_id(self, id):
        """
        Forms name of VD by givend VD id. No spaces allowed
        Returns: qsan-ssd3800-2_RAID10_10.48TB
        """
        raid = self._VDs.get(id)['raid'].replace(' ', '')
        name = self._VDs.get(id)['name'].replace(' ', '-')
        capacity = self._VDs.get(id)['capacity'].replace(' ', '')

        return '_'.join([name, raid, capacity])

    def vd_discovery(self):
        """
        Getting Volumes information from Storage
        Fills self._VDs with:
        {'id': {'name': '', 'capacity': '', 'raid': '', ... },
         'id': {'name': '', 'capacity': '', 'raid': '', ... }, ... }
        """
        page = 1
        VDs = {}

        while True:
            # Iteration over pages
            params = '&page=' + str(page)

            self._connection(self._url + self._url_path_VD + params,
                             username=None,
                             password=None,
                             data=None)

            VD_count = int(self._soup.response.find('vd_num').text)

            # Iteration over VDs
            for udv in self._soup.response.find_all('udv'):
                if udv:
                    attrs = {}
                    for attr in udv:

                        # Something wrong with img param or bs4 can't parse it
                        # .. <img/>no<vg_name>vgname</vg_name>
                        if attr.name and attr.name not in 'img':
                            attrs[attr.name] = attr.text

                    attrs.pop('id', None)

                    vd = {udv.find('id').text: attrs}
                    VDs.update(vd)
                else:
                    page += 1

            if len(VDs) == VD_count:
                break

        self._VDs = VDs

    def _vd_stats_enable_VDs(self, VDs):
        """
        Enables monitoring for specified VDs
        """
        p = '&volume_arr=' + ','.join([vd for vd in VDs])

        self._connection(self._url + self._url_path_select_stats_VD + p,
                         username=None,
                         password=None,
                         post=True,
                         data=None)

    def vd_stats(self):
        """
        Getting Volumes stats
        Returns: {'id': {'iops': 123, 'read': 123, 'write': 123}}
        """
        VDstats = {}

        volumes_IDs = [volume for volume in self._VDs]
        volumes_monitoring_check = []

        self._connection(self._url + self._url_path_VD_stats,
                         username=None,
                         password=None,
                         data=None)

        # Iteration over VDs
        for volume_stats in self._soup.response.find_all('volume_stats'):
            if volume_stats.vd_id:
                vid = volume_stats.find('vd_id').text
                stats = {
                    vid: {
                        'iops': volume_stats.find('iops_rate').text,
                        'read': volume_stats.find('tx_rate').text,
                        'write': volume_stats.find('rx_rate').text
                    }
                }
                volumes_monitoring_check.append(vid)

                # Converting to Bps
                stats[vid]['read'] = str(int(stats[vid]['read']) * 1024)
                stats[vid]['write'] = str(int(stats[vid]['write']) * 1024)

                VDstats.update(stats)

        # Enabling monitoring of unmonitored VDs
        if set(volumes_monitoring_check) != set(volumes_IDs):
            self._vd_stats_enable_VDs(volumes_IDs)

        return VDstats

    def disk_discovery(self):
        """
        Getting Disk information from Storage
        Fills self._DISKs with:
        {'id': {'slot': '', 'size': '', 'vendor': '', ... },
         'id': {'slot': '', 'size': '', 'vendor': '', ... }, ... }
        """

        DISKs = {}

        self._connection(self._url + self._url_path_DISK,
                         username=None,
                         password=None,
                         data=None)

        # Iteration over DISKs
        for hdd in self._soup.response.find_all('hdd'):
            if hdd:
                attrs = {}
                for attr in hdd:
                    attrs[attr.name] = attr.text

                attrs.pop('id', None)

                d = {hdd.find('id').text: attrs}
                DISKs.update(d)

        self._DISKs = DISKs

    def _disk_stats_enable_DISKs(self, DISKs):
        """
        Enables monitoring for specified DISKs
        """
        slots = []
        for disk in DISKs:
            slots.append(self._get_DISK_slot_by_id(disk))
        slots.sort()

        p = '&slot_arr=' + ','.join([slot for slot in slots])

        self._connection(self._url + self._url_path_select_stats_DISK + p,
                         username=None,
                         password=None,
                         post=True,
                         data=None)

    def disk_stats(self):
        """
        Getting Disks stats
        Returns: {id: {'latency': '123', 'thruput': '123'}}
        """
        DISKstats = {}

        disks_IDs = [disk for disk in self._DISKs]
        disks_monitoring_check = []

        self._connection(self._url + self._url_path_DISK_stats,
                         username=None,
                         password=None,
                         data=None)

        # Iteration over DISKs
        for disk_stats in self._soup.response.find_all('disk_monitor_stats'):
            if disk_stats.slot:
                slot = disk_stats.find('slot').text
                id = self._get_DISK_id_by_slot(slot)

                # Checking wether disk monitoring enabled or not
                if disk_stats.find('is_enabled').text == 'Yes':
                    disks_monitoring_check.append(id)

                stats = {
                    id: {
                        'latency': disk_stats.find('latency').text,
                        'thruput': disk_stats.find('thruput').text
                    }
                }

                # Converting to Bps
                stats[id]['thruput'] = str(int(stats[id]['thruput']) * 1024)

                DISKstats.update(stats)

        # Enabling monitoring of unmonitored DISKs
        if set(disks_monitoring_check) != set(disks_IDs):
            self._disk_stats_enable_DISKs(disks_IDs)

        return DISKstats

    def _get_DISK_id_by_slot(self, slot):
        """
        Returns: disk id by given slot
        """
        for disk in self._DISKs:
            if self._DISKs.get(disk)['slot'] == slot:

                return disk

    def _get_DISK_slot_by_id(self, id):
        """
        Returns: disk slot by given id
        """

        return self._DISKs.get(id)['slot']

    def _get_DISK_name_by_id(self, id):
        """
        Forms name of DISK by givend DISK id. No spaces allowed
        Returns: Slot_7_SEAGATE_ST3840FM0043
        """

        # F600Q (SANOS3?) doesn't have a model parameter
        if 'model' in self._DISKs.get(id):
            model = self._DISKs.get(id)['model']
        else:
            model = ''

        disk = (
            '_'.join(['Slot', self._DISKs.get(id)['slot'],
                      self._DISKs.get(id)['vendor'],
                      model, self._DISKs.get(id)['serial']
                      ])
        )

        return disk


class Zabbix():
    """
    Class for operationg with zabbix
    """
    _DATA = {'data': []}

    def __init__(self, qsan):
        """
        """
        self._qsan = qsan

    def print_storage_stats(self, zhost):
        """
        Returns:
        zhost	qsan.sanos4.storage.iops	123
        zhost	qsan.sanos4.storage.read	123
        zhost	qsan.sanos4.storage.write	123
        """
        for param, value in self._qsan.storage_stats().items():
            print('\t'.join([zhost,
                             'qsan.sanos4.storage.' + param,
                             value]))

    def print_vd_discovery(self):
        """
        Returns:
        {"data": [{"{#VOLUME}": "volname"}, ... ]}
        """
        for volume, params in self._qsan._VDs.items():
            element = {'{#VOLUME}': self._qsan._get_VD_name_by_id(volume)}
            self._DATA['data'].append(element)

        print(json.dumps(self._DATA, indent=2))

    def print_disk_discovery(self):
        """
        Returns:
        {"data": [{"{#DISK}": "diskname"}, ... ]}
        """
        for disk, params in self._qsan._DISKs.items():
            element = {'{#DISK}': self._qsan._get_DISK_name_by_id(disk)}
            self._DATA['data'].append(element)

        print(json.dumps(self._DATA, indent=2))

    def print_vd_stats(self, zhost):
        """
        Returns:
        zhost	qsan.sanos4.volume.iops[volname]	123
        zhost	qsan.sanos4.volume.read[volname]	123
        zhost	qsan.sanos4.volume.write[volname]	123
        ...
        """
        for volume, params in self._qsan.vd_stats().items():
            # get volume name
            n = self._qsan._get_VD_name_by_id(volume)

            for param, value in params.items():
                print('\t'.join([zhost,
                                 'qsan.sanos4.volume.' + param + '[' + n + ']',
                                 value]))

    def print_disk_stats(self, zhost):
        """
        Returns:
        zhost	qsan.sanos4.disk.iops[diskname]	123
        zhost	qsan.sanos4.disk.read[diskname]	123
        ...
        """
        for disk, params in self._qsan.disk_stats().items():
            # get disk name
            n = self._qsan._get_DISK_name_by_id(disk)

            for param, value in params.items():
                print('\t'.join([zhost,
                                 'qsan.sanos4.disk.' + param + '[' + n + ']',
                                 value]))

    def print_all_stats(self, zhost):
        """
        """
        self.print_vd_stats(zhost)
        self.print_storage_stats(zhost)
        self.print_disk_stats(zhost)


def main():
    """
    """
    args = argumentsparsing()

    qsan = QSAN(args.host, args.username, args.password)
    zabbix = Zabbix(qsan)

    if not args.zhost:
        args.zhost = 'zabbix host undefined'

    methods = {
        'discovery:volume': lambda: zabbix.print_vd_discovery(),
        'discovery:disk': lambda: zabbix.print_disk_discovery(),
        'stats:volume': lambda: zabbix.print_vd_stats(args.zhost),
        'stats:storage': lambda: zabbix.print_storage_stats(args.zhost),
        'stats:disk': lambda: zabbix.print_disk_stats(args.zhost),
        'stats:all': lambda: zabbix.print_all_stats(args.zhost)
    }

    m = methods.get(args.method)
    if m:
        m()


if __name__ == '__main__':
    main()
