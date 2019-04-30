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
                        help="Available methods: discovery:volume, " +
                             "discovery:disk, discovery:fc, discovery:cp,\n" +
                             "stats:volume, stats:storage, stats:disk, " +
                             "stats:cp, stats:all")
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
    Class for operationing with qsan
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
        self._url_path_FC = '/fc_x.php?ctrl_idx='
        self._url_path_select_stats_FC = '/monitor_x.php?op=fcport_set_monitor'
        self._url_path_select_stats_FC_SANOS3 = '/monitor_x.php'
        self._url_path_FC_stats = '/monitor_x.php?cmd=monitor_fcport'
        self._url_path_health = '/dashboard_x.php?query=system'
        self._url_path_health_SANOS3 = '/index.php'
        self._url_path_CP = '/ssd_cache_pool_x.php?query=getSSDtableData'
        self._url_path_CP_stats = '/ssd_cache_pool_x.php?query=get_statistics'
        self._SANOS_VERSION = 4
        self._username = username
        self._password = password
        self._VDs = {}
        self._DISKs = {}
        self._CPs = {}
        self._FCs = {}
        self.connect()
        self._sanos_version_detect()
        self.vd_discovery()
        self.disk_discovery()
        self.cache_pool_discovery()
        self.fc_discovery()

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
        except (RequestException, ConnectionError, ConnectTimeout,
                SSLError, MissingSchema, RetryError, ProxyError,
                InvalidHeader, ReadTimeout, UnrewindableBodyError,
                ChunkedEncodingError, HTTPError, StreamConsumedError,
                Timeout, TooManyRedirects, ContentDecodingError) as e:

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

    def _sanos_version_detect(self):
        """
        Detecting SANOS Version
        Sets self._SANOS_VERSION with int(major_version, ex.: 3). Default is 4
        """
        version_lookup = self._soup.find('div', id='logo_writing')
        if version_lookup:
            if 'SANOS 4.0' not in version_lookup.text:
                self._SANOS_VERSION = 3
        else:
            self._SANOS_VERSION = 3

    def is_storage_health_Good(self):
        """
        Method for getting storage health status
        Returns: True if Good, False if storage is in Degraded state or None
        if unable to check state
        """
        if self._SANOS_VERSION == 4:
            self._connection(self._url + self._url_path_health,
                             username=None,
                             password=None,
                             data=None)

            for item in self._soup.response.data.find_all('system'):
                if item.item.text == 'System Health':
                    if item.value.text == "Good":
                        return True
                    else:
                        return False

        elif self._SANOS_VERSION == 3:
            # SANOS3 Support
            self._connection(self._url + self._url_path_health_SANOS3,
                             username=None,
                             password=None,
                             data=None)

            status_div = self._soup.find('div', id='status_led')
            for el in status_div.find_all('input'):
                if '-green.gif' not in el['src']:
                    return False

            if status_div:
                return True

    def _get_VD_name_by_id(self, id):
        """
        Forms name of VD by givend VD id. No spaces allowed
        Returns: qsan-ssd3800-2_RAID10_10.48TB
        """
        raid = self._VDs.get(id)['raid'].replace(' ', '')
        name = self._VDs.get(id)['name'].replace(' ', '-')
        capacity = self._VDs.get(id)['capacity'].replace(' ', '')

        # F600Q (SANOS3?) compatibility
        if 'TB' not in capacity:
            new_capacity = str(round(float(capacity) / 1024 / 1024, 1))
            capacity = new_capacity + 'TB'

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
                        if attr.name:
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
                    if attr.name:
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

    def cache_pool_discovery(self):
        """
        Getting Cache Pools (CPs) information from Storage
        Fills self._CPs with:
        {'Name': {'rg_id': '', 'rg_name': '', 'ssd_name': '', ... },
         'Name': {'rg_id': '', 'rg_name': '', 'ssd_name': '', ... }, ... }
        """
        if self._SANOS_VERSION == 3:
            # Have no information about Cache Pools support in SANOS3
            return {}

        CPs = {}

        self._connection(self._url + self._url_path_CP,
                         username=None,
                         password=None,
                         data=None)

        # Iteration over Cache Pools
        for cp in self._soup.response.find_all('ssdpoollist'):
            if cp:
                attrs = {}
                for attr in cp:
                    if attr.name:
                        attrs[attr.name] = attr.text

                c = {cp.find('ssd_name').text.replace(' ', '-'): attrs}
                CPs.update(c)

        self._CPs = CPs

    def cp_stats(self):
        """
        Getting Cache Pool stats (separate stats for each
        ssd-enabled vd in pool)
        Returns: {'id': {'rg_id': '', 'name': '',
                         'rg_name': '', ..., 'stats': {'vd': {'p1': '',
                                                              'p2': '',
                                                              'pn': '', ... }},
                                                      {'vd': {'p1': '',
                                                              'p2': '',
                                                              'pn': '', ... }}
                                                       ... }}
        """
        if self._SANOS_VERSION == 3:
            # Have no information about Cache Pools support in SANOS3
            return {}

        Pools = {}
        Volume_Groups = {}

        self._connection(self._url + self._url_path_CP_stats,
                         username=None,
                         password=None,
                         data=None)

        # Pools
        for p in self._soup.response.find_all('pool_data'):
            if p.name:
                pool_id = p.find('rg_id').text

                attrs = {}
                for attr in p:
                    if attr.name:
                        attrs[attr.name] = attr.text

                pool = {pool_id: attrs}
                Pools.update(pool)

        # Volume Groups
        for vg in self._soup.response.find_all('vol_data'):
            if vg.name:
                vol_id = vg.find('vd').text

                attrs = {}
                for attr in vg:
                    if attr.name:
                        attrs[attr.name] = attr.text

                volume_group = {vol_id: attrs}
                Volume_Groups.update(volume_group)

        # Adding VG Volumes stats to Pools
        for pool, pool_params in Pools.items():
            # Can't check but in order with
            # https://www.qsan.com/en/software.php?no=A90B71B5
            # it may be more than one volume (vd) in Raid Group/Volume
            # group served by Pool
            pool_params['stats'] = {}

            for v, volume_params in Volume_Groups.items():
                if volume_params['rg'] == pool_params['rg_id']:
                    pool_params['stats'].update({v: volume_params})

        return Pools

    def cp_stats_summarize(self):
        """
        Getting some of Cache Pool stats in summary by Volumes as
        one cache pool can serve more than one Volume in Raid/Volume group
        Returns: {'cachepoolname': {'log_rd_hit': '',
                                    'log_rd_tot': '',
                                    'size_alloc': '',
                                    'size_cached': '',
                                    'size_dirty': '',
                                    'ratio': ''}}
        """
        cp_stats = self.cp_stats()
        stats = {}

        for cp_params in cp_stats.values():
            size_alloc = 0   # Total cache size
            size_cached = 0  # Bytes cached
            size_dirty = 0   # Bytes dirty
            log_rd_hit = 0   # Cache hits
            log_rd_tot = 0   # Total hits
            for cp_vol_params in cp_params['stats'].values():
                size_alloc = int(cp_vol_params['size_alloc']) * 1024 * 1024
                size_cached += int(cp_vol_params['size_cached']) * 1024 * 1024
                size_dirty += int(cp_vol_params['size_dirty']) * 1024 * 1024
                log_rd_hit += int(cp_vol_params['log_rd_hit'])
                log_rd_tot += int(cp_vol_params['log_rd_tot'])

            ratio = round(log_rd_hit / (log_rd_tot / 100))
            vol = {cp_params['name']: {
                'size_alloc':   str(size_alloc),
                'size_cached':  str(size_cached),
                'size_dirty':   str(size_dirty),
                'log_rd_hit':   str(log_rd_hit),
                'log_rd_tot':   str(log_rd_tot),
                'ratio':        str(ratio)
            }}

            stats.update(vol)

        return stats

    def fc_discovery(self):
        """
        Getting FC Ports information from Storage
        Fills self._FCs with:
        {'slot:port': {'name': '', 'status': '', 'data_rate': '', ... },
         'slot:port': {'name': '', 'status': '', 'data_rate': '', ... }, ... }
        """
        FCs = {}

        # Iteration over Controllers
        for controller in ['0', '1']:
            self._connection(self._url + self._url_path_FC + controller,
                             username=None,
                             password=None,
                             data=None)

            # If Storage has Fibre Channel ports
            if self._soup.response:
                # Iteration over ports
                for fcp in self._soup.response.find_all('fc_port_value'):
                    if fcp:
                        attrs = {}
                        for attr in fcp:
                            if attr.name:
                                attrs[attr.name] = attr.text

                        # SANOS3 support
                        if self._SANOS_VERSION == 3:
                            port_id = str(int(attrs.get('name')[5:6]) - 1)
                        else:
                            port_id = str(int(attrs.get('name')[2:3]) - 1)

                        p = {':'.join([controller, port_id]): attrs}

                        FCs.update(p)

        self._FCs = FCs

    def _fc_stats_enable_FCs(self, FCs):
        """
        Enables monitoring for specified FC ports
        """
        FCs.sort()

        if self._SANOS_VERSION == 4:
            p = '&fibre_arr=' + ','.join([fc for fc in FCs])

            self._connection(self._url + self._url_path_select_stats_FC + p,
                             username=None,
                             password=None,
                             post=True,
                             data=None)
        else:
            # SANOS 3
            PARAMS = {
                'ctrl_idx': None,
                'is_enable': 1,
                'op': 'fcport_set_monitor',
                'port_idx': None
                }
            for slotport in FCs:
                PARAMS['ctrl_idx'] = slotport[0:1]
                PARAMS['port_idx'] = slotport[2:3]

                self._connection(self._url +
                                 self._url_path_select_stats_FC_SANOS3,
                                 username=None,
                                 password=None,
                                 post=True,
                                 data=PARAMS)

    def fc_stats(self):
        """
        Getting FC ports stats
        Returns: {'slot:port': {'tx': '123', 'rx': '123'}}
        """
        FCstats = {}

        ports_IDs = [port for port in self._FCs]
        ports_monitoring_check = []

        self._connection(self._url + self._url_path_FC_stats,
                         username=None,
                         password=None,
                         data=None)

        # Iteration over Controllers
        for ctrl_fcport in self._soup.response.find_all('ctrl_fcport_info'):
            if ctrl_fcport:
                controller = ctrl_fcport.find('ctrl_idx').text

                # Iteration over FC Ports
                for fcport_stats in ctrl_fcport.find_all('fcport_stats'):
                    if fcport_stats:
                        port = fcport_stats.find('port_idx').text
                        id = controller + ':' + port

                        # Checking wether port monitoring enabled or not
                        if fcport_stats.find('is_enabled').text == 'Yes':
                            ports_monitoring_check.append(id)

                            if int(fcport_stats.find('num_rates').text) > 0:
                                stats = {
                                    id: {
                                        'tx': fcport_stats.find('tx').text,
                                        'rx': fcport_stats.find('rx').text
                                    }
                                }

                                # Converting to Bps
                                stats[id]['tx'] = str(int(stats[id]['tx']) *
                                                      1024)
                                stats[id]['rx'] = str(int(stats[id]['rx']) *
                                                      1024)
                            else:
                                stats = {id: {'tx': '0', 'rx': '0'}}

                        else:
                            stats = {id: {'tx': '0', 'rx': '0'}}

                        FCstats.update(stats)

        # Enabling monitoring of unmonitored FCs
        if set(ports_monitoring_check) != set(ports_IDs):
            if self._SANOS_VERSION == 4:
                self._fc_stats_enable_FCs(ports_IDs)
            else:
                # SANOS3
                diff = set(ports_IDs) - set(ports_monitoring_check)
                self._fc_stats_enable_FCs(list(diff))

        return FCstats

    def _get_FC_port_name_by_id(self, port):
        """
        Forms name of FC port by givend port id. No spaces allowed
        Returns: CTR2_FC4_(16Gb)
        """
        fcport = (
            '_'.join([self._FCs.get(port)['ctr'],
                      self._FCs.get(port)['name']
                      ]).replace(' ', '_')
        )

        return fcport


class Zabbix():
    """
    Class for operationing with zabbix
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
        for volume in self._qsan._VDs:
            element = {'{#VOLUME}': self._qsan._get_VD_name_by_id(volume)}
            self._DATA['data'].append(element)

        print(json.dumps(self._DATA, indent=2))

    def print_disk_discovery(self):
        """
        Returns:
        {"data": [{"{#DISK}": "diskname"}, ... ]}
        """
        for disk in self._qsan._DISKs:
            element = {'{#DISK}': self._qsan._get_DISK_name_by_id(disk)}
            self._DATA['data'].append(element)

        print(json.dumps(self._DATA, indent=2))

    def print_cp_discovery(self):
        """
        Returns:
        {"data": [{"{#CACHEPOOL}": "cpname"}, ... ]}
        """
        for cp in self._qsan._CPs:
            element = {'{#CACHEPOOL}': cp}
            self._DATA['data'].append(element)

        print(json.dumps(self._DATA, indent=2))

    def print_fc_discovery(self):
        """
        Returns:
        {"data": [{"{#FCPORT}": "portname"}, ... ]}
        """
        for port in self._qsan._FCs:
            element = {'{#FCPORT}': self._qsan._get_FC_port_name_by_id(port)}
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

    def print_cp_stats(self, zhost):
        """
        Returns:
        zhost	qsan.sanos4.cachepool.size_alloc[cpname]	123
        zhost	qsan.sanos4.cachepool.size_cached[cpname]	123
        zhost	qsan.sanos4.cachepool.size_dirty[cpname]    123
        zhost	qsan.sanos4.cachepool.log_rd_hit[cpname]    123
        zhost	qsan.sanos4.cachepool.log_rd_tot[cpname]	123
        zhost	qsan.sanos4.cachepool.ratio[cpname]	        123
        """

        for cp, cp_params in self._qsan.cp_stats_summarize().items():
            for cp_param, cp_param_value in cp_params.items():
                print('\t'.join([zhost,
                                 'qsan.sanos4.cachepool.' + cp_param +
                                 '[' + cp + ']',
                                 cp_param_value]))

    def print_fc_stats(self, zhost):
        """
        Returns:
        zhost	qsan.sanos4.fcport.tx[portname]	123
        zhost	qsan.sanos4.fcport.rx[portname]	123
        ...
        """
        for port, params in self._qsan.fc_stats().items():
            # get port name
            n = self._qsan._get_FC_port_name_by_id(port)

            for param, value in params.items():
                print('\t'.join([zhost,
                                 'qsan.sanos4.fcport.' + param + '[' + n + ']',
                                 value]))

    def print_all_stats(self, zhost):
        """
        """
        self.print_vd_stats(zhost)
        self.print_storage_stats(zhost)
        self.print_disk_stats(zhost)
        self.print_fc_stats(zhost)
        self.print_cp_stats(zhost)


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
        'discovery:fc': lambda: zabbix.print_fc_discovery(),
        'discovery:cp': lambda: zabbix.print_cp_discovery(),
        'stats:volume': lambda: zabbix.print_vd_stats(args.zhost),
        'stats:storage': lambda: zabbix.print_storage_stats(args.zhost),
        'stats:disk': lambda: zabbix.print_disk_stats(args.zhost),
        'stats:cp': lambda: zabbix.print_cp_stats(args.zhost),
        'stats:all': lambda: zabbix.print_all_stats(args.zhost)
    }

    m = methods.get(args.method)
    if m:
        m()


if __name__ == '__main__':
    main()
