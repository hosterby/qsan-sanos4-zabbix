# Zabbix Template for QSAN SANOS4

## About
Script and template for monitoring QSAN SANOS4 based storage systems in [Zabbix](http://zabbix.com)

## Features
Supports:
 * Overall stats: IOPS, Throughput (Read, Write)
 * Low Level Discovery of:
   * Volumes
   * Disks
   * FC Ports
 * Automatic enabling of monitoring for unmonitored Volumes and Disks
 * Statistics:
   * Volumes: IOPS, Throughput (Read, Write)
   * Disks: Latency, Throughput
   * FC Ports: Throughput

## Requirements
 * Zabbix-server version 2.0+
 * Python version 2.7+ (tested on 2.7, 3.5, 3.6)
 * Python pip modules: bs4 requests lxml
 * SANOS4 based QSAN Storage system (tested on XS3224, XS3226)
 * SANOS3 based QSAN Storage system (tested on F600Q, no Overall and Volume stats)

## Installation
1. Install python dependencies
```
zabbix@monitoring:~$ pip install `cat requires.txt`
```
2. Clone script to your Zabbix server's external scripts directory
```
cd /etc/zabbix/externalscripts/
git clone https://github.com/hosterby/qsan-sanos4-zabbix
```

3. Create cron rule for sending traps
```
* * * * * ( /etc/zabbix/externalscripts/qsan-sanos4-zabbix/qsan.py --host <storage_IP_or_FQDN> --zhost <Storage_Zabbix_name> --method stats:all | /usr/bin/zabbix_sender -z <IP_of_Zabbix_traps_receiver> -i - > /dev/null 2>&1 )
```
Each of your storage requires separate run of qsan.py

Change `qsan.py` with `qsan.sh` if you're using Python pyenv virtual environments. Move `qsan.sh.edit` to `qsan.sh` editing environment name.

4. Upload template XML file `zbx_template_qsan_sanos4.xml` to Zabbix web interface
5. Create a host using uploaded teplate with a name `<Storage_Zabbix_name>`
6. If you've configured your storage with non default read-only user `user`:
* Add `[--username USERNAME] [--password PASSWORD]` parameters to cron command replacing `USERNAME` and `PASSWORD` with your Storage credentials.
* Add `"--username", {$QSAN.USERNAME},"--password", {$QSAN.PASSWORD}` into template `Discovery rules` parameters `Key` field.
* Add two host Macroses `{$QSAN.USERNAME}` and `{$QSAN.PASSWORD}` and fill it with your Storage credentials

## Using as library
```
$ python
Python 3.6.6 (default, Jul 25 2018, 10:34:32)
[GCC 7.3.0] on linux
Type "help", "copyright", "credits" or "license" for more information.
>>> from qsan import QSAN
>>>
>>> storage = QSAN('10.0.148.9')
>>> for diskid in storage._DISKs:
...     print(diskid)
...
1043062069
1548761799
2866439684
3366027766
1446237940
136600410
3749489499
>>> somedisk = storage._DISKs.get('1446237940')
>>> somedisk
{'slot': '5', 'size': '3.49 TB', 'health': 'Good', 'fw_ver': '0007', 'rate': 'SAS SSD    12.0Gb/s', ... }
>>> somedisk['health']
'Good'
```

---
:copyright: 2018 Ivan Semernik @ hoster.by

Licensed under the MIT License
